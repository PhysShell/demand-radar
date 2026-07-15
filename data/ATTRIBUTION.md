# Attribution

The evidence in this data branch (`data/evidence.jsonl`,
`data/provenance.jsonl`) originates from Stack Overflow
(https://stackoverflow.com), collected via the official Stack Exchange
API v2.3 (`/search/advanced`).

- Every accepted question's real author, direct post URL, and content
  license are preserved in `data/provenance.jsonl`
  (`owner_user_id`, `owner_display_name`, `owner_profile_url`,
  `question_url`, `content_license`) -- `data/evidence.jsonl`'s own
  `author` field is a stable `stackoverflow:user:<id>` identifier, not
  a display name, by design (see RawEvidenceRecord's contract);
  `provenance.jsonl` is where the human-readable attribution lives.
- Import is limited to questions created on or after 2018-05-02 UTC
  (`from_date: 2018-05-02` in the request file).
- This text is used as research evidence for opportunity discovery,
  not republished, redistributed, or presented as this project's own
  writing.
- Any downstream report, opportunity card, or analysis that cites a
  Stack Overflow question MUST preserve and display the direct post
  URL (`question_url` in provenance, or `url` in the evidence
  record) alongside the citation -- never paraphrase without a link
  back to the original post.
