# Dedust — rules for AI sessions

Read `knowledge_base/README.md` (the doc index + assistant conventions)
before working. `knowledge_base/00_capabilities.md` says what exists;
`knowledge_base/12_version_history.md` is the change log.

**Non-negotiable rules:**

1. **`git pull` before starting, and read the status lines of the relevant
   plan docs** (they say what is already built). Never build from session
   memory alone.
2. **Every commit updates the documentation, in the same commit**: append
   one line to `knowledge_base/12_version_history.md`; if the commit changes
   what Dedust can do, also update `knowledge_base/00_capabilities.md`.
3. Land changes via git (branch merge / commit in place), never by copying
   files between checkouts.
4. The server may be in live use by an RA — never restart it or write into
   `projects/` without asking. Verify with pytest
   (`C:\Users\agaspar\anaconda3\Scripts\pytest.exe tests -q`) and throwaway
   `_smoke_`-prefixed fixture projects (delete them afterwards).
5. `projects/` holds live production data with hand corrections — never run
   destructive scripts against it.
