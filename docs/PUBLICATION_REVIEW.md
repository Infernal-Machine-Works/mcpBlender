# Publication review

Review date: 2026-11-01. No publication or push was performed.

Current source uses **PQC** for the internal agent client. The project working
rules preserve this terminology. The authentication interoperability test takes
an optional `MCPBLENDER_TEST_FREECAD_KEY_MODULE` path rather than embedding a
private checkout location. Run it without the variable for ordinary portable
checks, or supply the sibling module explicitly for an interoperability check.

The review examined tracked source, new deployment files, reachable Git file
history, commit messages and built wheel/source archive contents. Searches covered
credential-like assignments, private-key blocks, private addresses, internal
hostnames and absolute account paths. No apparent live credentials or private
keys were found. The long credential literal in the building workflow is a
deliberately public loopback fixture value, not a production credential. This is
a source review and pattern scan, not proof that every possible secret is absent.

**History still needs cleanup before making this repository public.** Older
revisions of `tests/test_server.py` contain a private absolute checkout path;
older README revisions contain the expanded internal client name. A corrective
commit removes those details from current source but does not erase history.
Keep the existing repository private until deciding whether to rewrite that
history or publish a separately reviewed clean snapshot. Existing local history
was preserved. Git author identities and timestamps also remain publication
metadata; the explicitly requested distribution name remains unchanged.

Actual server/client configuration, issued credentials, configuration backups,
host service settings, runtime environments, logs, evidence and models are kept
outside the checkout. The local ToDo is excluded from Git. Recheck the exact
staged tree, final artifacts and history immediately before publishing; new files
or future changes can invalidate this review.
