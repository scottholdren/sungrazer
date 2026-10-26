# Working on this repo

## Results go in the repo

Commit result outputs (scorecards, track lists, contact sheets, reports, notes)
as files under `results/` and push them to github.com/scottholdren/sungrazer,
rather than only printing them in the terminal. This applies to everything,
not just scoring: the owner reviews the work from GitHub.

After any run that produces results, commit and push the code first, so the
scorecard header records a clean commit. Then commit and push the results.
Pushing is standing practice for this repo. Large regenerable data stays in
the gitignored `data/`.

## Standing constraints

- Never poll any server more often than every 15 min. Cache everything on
  disk; never re-download a file you already have. Send a descriptive
  User-Agent.
- Python is `.venv/bin/python` (3.12); there is no bare `python`. The CLI is
  `python -m kreutz <cmd>`.
