# Binary Sources are stored in Git LFS

Every binary Source (PDF, ebook, audio, video, ...) is stored in Git LFS; Canonical Documents and Assets stay in normal git. GitHub blocks normal files over 100 MiB, and a repo full of originals grows too large for every clone; with LFS, agents clone only Markdown and images (`GIT_LFS_SKIP_SMUDGE=1`) and download originals on demand. The cost is GitHub's LFS quota (on the Free plan: 10 GiB storage, 10 GiB transfer per month, 2 GB per file), so the app shows LFS usage and warns before an upload goes over it.
