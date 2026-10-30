"""
Intake: getting a job's IC summary from Studio into NG Jobs, verified first (design 3.5, Fetch).

summary_check decides, from the PDF alone, whether a downloaded render is complete in itself, and
fingerprints its text (content_fingerprint). A pass is not enough to file: filing also needs two
fetches at least 10 minutes apart whose fingerprints are equal (AGENTS.md: Before filing goes live).
"""
