"""
The P2 reader evaluation (docs/p2/build-spec.md 5.7; design 3.6 and decisions 10, 16 and 17).

Why it exists: Phase 5 would have a model read elevation drawings overnight, with code confirming
every value. Whether that is worth 28 dev-days is decided here, on archived walls whose as-cut
answer is known, before any of it is built. It is a dev tool: nothing in the app imports it, and
nothing in it writes outside .local-fixtures\\reader\\ (gitignored), because crops, truth and
reports all name clients' rooms.

The steps, one module each:
  prep      a summary's elevation rasters at native pixels, each wall's parts-list lines, and the
            proof that no text span overlaps a crop (containment, design 3.6)
  truth     the as-cut panels per wall, read from a .des by bytes
  pairing   which crop is which .des wall; anything not sure is left out and counted, never guessed
  payload   what would be sent: Jim's terms, the parts list as the allowed values, worked examples,
            and a scan that blocks any payload carrying a client, address or designer detail
  scoring   accept and error rates per wall category, silent errors apart, jobs fully built, cost
  budget    the per-run dollar cap, checked before every call
  providers OpenAI and Anthropic behind one interface; keys from Windows Credential Manager
  placed    the cached answers re-scored with P5a's placement rules, and the rules on the frozen jobs
  run       the command line: collect, prep, pair, dry-run, eval, score, place
"""
