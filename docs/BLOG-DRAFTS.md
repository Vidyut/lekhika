# Blog posts (vidyut.info)

Uploaded and published on 2026-11-12. The existing landing page was updated in place and the guide sections were published as child guide posts. Add screenshots (replace `[Screenshot: ...]` placeholders), review formatting/internal links for the web, and adjust ordering/navigation/parenting in WordPress as desired.

## Published pages/posts

| Page | ID | Link | Status |
|---|---:|---|---|
| Lekhak (landing page, updated in place) | 9135 | https://vidyut.info/downloads/lekhak/ | publish |
| Getting started with Lekhak | 9146 | https://vidyut.info/blog/2026/11/12/getting-started-with-lekhak/ | publish |
| Write and edit your manuscript | 9147 | https://vidyut.info/blog/2026/11/12/write-and-edit-your-manuscript/ | publish |
| Organise your book | 9148 | https://vidyut.info/blog/2026/11/12/organise-your-book/ | publish |
| Templates and export | 9149 | https://vidyut.info/blog/2026/11/12/templates-and-export/ | publish |
| Import your work and keep it safe | 9150 | https://vidyut.info/blog/2026/11/12/import-your-work-and-keep-it-safe/ | publish |
| Optional tools and settings | 9151 | https://vidyut.info/blog/2026/11/12/optional-tools-and-settings/ | publish |
| Support Lekhak | 9152 | https://vidyut.info/blog/2026/11/12/support-lekhak/ | publish |
| Lekhak (extra landing draft, not used) | 9145 | https://vidyut.info/?p=9145 | draft |

## Structure

- **Landing page:** https://vidyut.info/downloads/lekhak/ — key features for writers, install, and links to guide sections.
- **Guide sections:** Getting started → Write and edit → Organise your book → Templates and export → Import your work and keep it safe → Optional tools and settings → Support.

## Notes

- Content generated from the restructured guide (`~/Documents/Lekhak Guide.lekhak`, shipped to `src/lekhak/guide/guide.lekhak`) with requested copy tweaks (opt-in updates, clearer paste/spell-check/AI wording).
- Upload/update performed via WordPress REST API (`scripts/upload_blog.py --site https://vidyut.info`, plus direct API updates). A `User-Agent` header was added to `scripts/upload_blog.py` to avoid Cloudflare 1010 blocks.
- Author set to site user ID 5382 on updated/published items; landing page updated in place and published.
- Screenshot placeholders replaced on published pages with figure/caption slots and in-content guide navigation (landing/previous/next) added. Existing WordPress media screenshots inserted where they fit (landing hero/editor, Getting started editor, Write and edit page view, Organise your book corkboard/outliner). Remaining landing slots still need new captures: export dialog/templates, paste preferences, tracked changes/AI suggestions, and applications menu/first launch. Replace those with actual screenshots and review/polish formatting/internal links in WordPress as needed.
