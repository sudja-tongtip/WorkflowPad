# Songcraft lessons (Songwriting Academy)

These files are the lessons shown under the eight aspects of the Song Critic page. You can open and edit them. The program never overwrites a file you changed.

- `opening.md`, `theme.md`, `section_purpose.md`, `development.md`, `hook.md`, `imagery.md`, `repetition.md`, `ending.md`: the Thai lessons (the default language).
- the same names with `.en.md` (for example `opening.en.md`): the English lessons, shown when the program language is English. When one is missing the Thai lesson is shown.
- The topic id in the front matter (`opening`, `theme`, `sections`, `development`, `hook`, `imagery`, `repetition`, `ending`) is fixed: it is how a lesson is matched with an aspect of the report. Do not rename it.

## Format

```
---
id: opening
aspect: opening
version: 1.0.0
title: the title shown
language: th
---
## What It Is
## Why It Works
## How to Use
(text, then techniques:)
### scene | Scene Opening
text of the technique
## Examples
## Common Pitfalls
## Try It Yourself
## References
- R1 | title | author or publisher | supports: what this source supports | status: verified | url: https://...
```

- The seven `##` headings are fixed English words (the text under them is in the lesson language).
- A `###` heading inside "How to Use" starts one technique: `### stable-id | Title`.
- `[R1]` inside the text marks a principle that reference R1 supports. Text with no tag is this program's own synthesis. The Examples are written for teaching.
- A reference has `status: verified` (the book or the course page was looked up) or `status: general` (general craft knowledge, not a citation).
- If a file is not valid (no front matter, a missing section, a `[R3]` with no R3 ...) the program tells you what is wrong, shows the default lesson instead and keeps your file as it is.
- To get the default back, delete your copy: it is made again at the next start. Raise `version` when you change a lesson in a way you want to track.
