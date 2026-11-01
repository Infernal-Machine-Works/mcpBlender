"""Frontend-only recipe validation and compilation; no eval or Python templates."""
import copy
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, FiniteFloat, create_model, model_validator

from .commands import COMMANDS
from .config import Model

# No code, network credentials, process lifecycle, nested actions or project reloads.
BLOCKED = {"execute_code", "run_action", "open_blend", "checkpoint", "shutdown_blender",
           "download_asset", "upload_asset", "request_asset_api"}
ALLOWED = set(COMMANDS) - BLOCKED


class Input(Model):
    type: Literal["string", "integer", "number", "boolean", "string_list", "vector3", "color"]
    description: str = Field(min_length=1, max_length=1000)
    required: bool = True
    default: object = None
    minimum: float | None = None
    maximum: float | None = None
    min_length: int = Field(default=1, ge=0, le=1000)
    max_length: int = Field(default=255, ge=1, le=1000)
    choices: list[str] | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_options(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Input minimum exceeds maximum")
        if self.min_length > self.max_length:
            raise ValueError("Input min_length exceeds max_length")
        if self.choices and self.type != "string":
            raise ValueError("choices require a string input")
        if not self.required and "default" not in self.model_fields_set:
            raise ValueError("Optional inputs require an explicit default")
        return self


class Step(Model):
    id: str = Field(pattern="^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    command: str
    params: dict = Field(default_factory=dict)


class Recipe(Model):
    name: str = Field(pattern=r"^custom\.[a-zA-Z][a-zA-Z0-9_.-]{0,55}$")
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=4000)
    inputs: dict[str, Input] = Field(default_factory=dict, max_length=64)
    steps: list[Step] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_steps(self):
        if len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError("Action step IDs must be unique")
        for name in self.inputs:
            if not name.isidentifier() or name.startswith("_"):
                raise ValueError("Input names must be public Python-style identifiers")
        for step in self.steps:
            if step.command not in ALLOWED:
                raise ValueError(f"Command is not allowed in recipes: {step.command}")
            validate_template(step.params, self.inputs)
        return self


class RecipeFile(Model):
    format_version: Literal[1] = 1
    actions: list[Recipe] = Field(min_length=1, max_length=64)


def validate_template(value, inputs):
    if isinstance(value, dict):
        if any(key.startswith("$") for key in value):
            if set(value) != {"$input"} or not isinstance(value["$input"], str) or value["$input"] not in inputs:
                raise ValueError("Only whole-value {$input: name} references are supported")
        else:
            for item in value.values():
                validate_template(item, inputs)
    elif isinstance(value, list):
        for item in value:
            validate_template(item, inputs)


def substitute(value, inputs):
    if isinstance(value, dict):
        if set(value) == {"$input"}:
            return copy.deepcopy(inputs[value["$input"]])
        return {key: substitute(item, inputs) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, inputs) for item in value]
    return value


def input_model(recipe):
    fields = {}
    for name, spec in recipe.inputs.items():
        numeric = {"ge": spec.minimum, "le": spec.maximum}
        if spec.type == "string":
            annotation = Literal[tuple(spec.choices)] if spec.choices else Annotated[str, Field(min_length=spec.min_length, max_length=spec.max_length)]
        elif spec.type in {"integer", "number"}:
            annotation = Annotated[int if spec.type == "integer" else FiniteFloat, Field(**numeric)]
        elif spec.type == "boolean":
            annotation = bool
        elif spec.type == "string_list":
            annotation = Annotated[list[Annotated[str, Field(min_length=1, max_length=255)]], Field(min_length=spec.min_length, max_length=spec.max_length)]
        else:
            count = 3 if spec.type == "vector3" else 4
            component = FiniteFloat if count == 3 else Annotated[FiniteFloat, Field(ge=0, le=1)]
            annotation = Annotated[list[component], Field(min_length=count, max_length=count)]
        fields[name] = (annotation, Field(default=... if spec.required else spec.default, description=spec.description))
    return create_model(recipe.name.replace(".", "_"), __config__=ConfigDict(extra="forbid", strict=True, validate_default=True), **fields)


class Definition:
    def __init__(self, name, version, description, model, commands, compiler, fingerprint, required_commands=None):
        self.name, self.version, self.description, self.model = name, version, description, model
        self.commands, self.compiler = set(commands), compiler
        self.required_commands = set(commands if required_commands is None else required_commands)
        if not self.required_commands <= self.commands:
            raise ValueError("Required commands must be declared as possible commands")
        self.digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def describe(self):
        capabilities = sorted({flag for command in self.commands for flag in COMMANDS[command][1]})
        return {"name": self.name, "version": self.version, "definition_sha256": self.digest,
                "description": self.description, "input_schema": self.model.model_json_schema(),
                "required_commands": sorted(self.required_commands),
                "possible_commands": sorted(self.commands), "possible_capabilities": capabilities}


class Registry:
    def __init__(self, files=()):
        self.definitions = {}
        from .workflows import definitions
        for definition in definitions():
            self.add(definition)
        for filename in files:
            path = Path(filename).expanduser()
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("Recipe file exceeds 1 MiB")
            document = RecipeFile.model_validate_json(path.read_text())
            for recipe in document.actions:
                model = input_model(recipe)
                definition = Definition(recipe.name, recipe.version, recipe.description, model,
                    [s.command for s in recipe.steps],
                    lambda values, r=recipe: {"steps": [s.model_dump() | {"params": substitute(s.params, values)} for s in r.steps]},
                    recipe.model_dump())
                self.add(definition)

    def add(self, definition):
        if definition.name in self.definitions:
            raise ValueError(f"Duplicate action name: {definition.name}")
        self.definitions[definition.name] = definition

    def get(self, name):
        if name not in self.definitions:
            raise ValueError("Unknown named action; use list_actions")
        return self.definitions[name]

    def compile(self, name, inputs):
        definition = self.get(name)
        values = definition.model.model_validate(inputs, strict=True).model_dump(exclude_none=True)
        plan = definition.compiler(values)
        steps = plan["steps"]
        if not 1 <= len(steps) <= 64 or len({s["id"] for s in steps}) != len(steps):
            raise ValueError("Actions require 1 to 64 uniquely named steps")
        for step in steps:
            if step["command"] not in ALLOWED or step["command"] not in definition.commands:
                raise ValueError("Action compiler emitted an undeclared or forbidden command")
            COMMANDS[step["command"]][0].model_validate(step["params"])
        return plan | {"action": name, "version": definition.version, "definition_sha256": definition.digest, "inputs": values}
