# Follow-up work

Primary workflows: animation production; game/simulation asset modeling; model
visualization. Deployment follows qualification of the needed core workflows.

- Add representative physics-bake fixtures when selected. Small skeletal and
  relative morph fixtures and structured clip tools are now qualified; see
  VALIDATION.md for their scope and evidence.
- Verify GLB import in the resumed Godot project: units, axes, UVs/textures, animation
  clips, shader fidelity and collision needs. Export alone is insufficient.
- Expand evaluated topology, UV and deformation helpers to more varied assets and
  larger workloads; the first structured asset/rig workflows are qualified.
- Qualify concrete external generation/diffusion services when endpoints are chosen.
  Generic authenticated JSON/download/upload adapters are implemented; specific
  vendor and multipart protocols remain separate work.
- Consider background render workers when the GUI must remain responsive. Current
  native rendering occupies Blender while the separate broker remains usable.
- Qualify more Blender/platform versions; current evidence is FreeBSD Blender 5.0.1.
- Add explicit journal/artifact archival and retention controls for permanent use.
  History/output currently remain on disk with live storage reporting.
- Qualify external HTTPS deployment and install skills/client configuration when
  permanent deployment is requested.
