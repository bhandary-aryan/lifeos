# Real-browser QA sweep

pytest checks the HTML a page returns. It cannot see layout, so it cannot tell you that a table spills off a phone screen,
that chart labels are unreadable on the dark theme, or that today's checkbox is scrolled out of view. This sweep drives a real
browser to find those.

```bash
python manage.py seed_demo --password demo-Pass-12345     # development only; creates demo@demo.lifeos.invalid
python manage.py runserver 8000 &
cd qa && npm install                                       # downloads Chrome via puppeteer
QA_EMAIL=demo@demo.lifeos.invalid QA_PASSWORD=demo-Pass-12345 node visual_sweep.js
```
It checks about 25 pages at desktop (1366px) and phone (390px) sizes in light and dark themes and **exits 1** on horizontal
overflow, JavaScript errors, failed requests, HTTP errors, or tap targets under 24px. Screenshots go to `qa/out/` — **open a few
and look at them**; the checks only catch what they were written to catch.

Options (env): `BASE_URL`, `QA_PAGES` (JSON list of `[name, path]`), `QA_MODES`, `QA_OUT`, `CHROME_PATH` (use an installed browser).
