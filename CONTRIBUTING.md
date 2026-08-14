# Contributing

Thank you for helping researchers create safer, more traceable journal manuscripts.

Before opening a pull request:

1. keep `skills/anything-to-journal/` as the only canonical skill source;
2. keep `SKILL.md` frontmatter limited to `name` and `description`;
3. use Python's standard library unless a dependency is essential and documented;
4. add a synthetic fixture/test for extraction or audit changes;
5. never commit real user materials, unpublished results, personal data, publisher logos, or proprietary templates;
6. run `python3 -m unittest discover -s tests -v` and the Skill quick validator;
7. treat missing figures, tables, citations, equations, or build evidence as hard failures, never silent warnings.
8. keep `README.md` and `README.zh-TW.md` complete and semantically aligned whenever behavior changes.

Security-sensitive issues involving malicious documents, archives, templates, or path handling should be reported privately to the repository maintainer once a public hosting location is configured.
