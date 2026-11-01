# Project working rules

- Commit each completed feature separately to this local Git repository. Keep
  unrelated work out of that feature's commit and run the relevant checks first.
- Every commit must explicitly suppress signing, even though signing is enabled
  by default. Use `git commit --no-gpg-sign -m "..."` (including when amending).
  Do not change the user's signing configuration to accomplish this.
- Keep commits local. Push only when the user explicitly requests it.
- Preserve user changes and stage only the files or hunks belonging to the work
  being committed.
