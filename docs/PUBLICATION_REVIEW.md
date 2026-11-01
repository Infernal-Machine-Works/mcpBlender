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

**The local publication history on `master` has been cleaned.** Older test
revisions now use a generic example checkout path, and README revisions use PQC.
The rewrite preserved all 33 feature commits, their authors/dates/messages and
the exact final tree. The original history was retained in a private bundle
outside the checkout. The public-history scan covers only `master`: old remote
tracking refs, reflogs and the backup are intentionally private and must not be
published. Git author identities and timestamps remain publication metadata;
the explicitly requested distribution name remains unchanged.

An existing remote `master` still points to the earlier history. Replacing it
requires explicit authorization for a non-fast-forward update and a lease tied
to its verified tip. Push only `master`, without tags or private backup refs.
History replacement does not erase copies previously fetched elsewhere.

Actual server/client configuration, issued credentials, configuration backups,
host service settings, runtime environments, logs, evidence and models are kept
outside the checkout. The local ToDo is excluded from Git. Recheck the exact
staged tree, final artifacts and history immediately before publishing; new files
or future changes can invalidate this review.
