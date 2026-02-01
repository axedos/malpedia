# malpedia-references

Downloads and parses the Malpedia bibliography into a SQLite database. Can also scrape the referenced URLs and save the content locally.

## Setup

```bash
pip install requests
```

## Usage

Download the bibliography and import it:
```bash
python bib_to_db.py --download
```

Import from a local file:
```bash
python bib_to_db.py malpedia.bib
```

Import and scrape all URLs:
```bash
python bib_to_db.py --download -s
```

Scrape with a limit (useful for testing):
```bash
python bib_to_db.py --scrape-only -l 50
```

Resume scraping from a specific index:
```bash
python bib_to_db.py --scrape-only --start 100
```

## Options

| Flag              | Description                                      |
|-------------------|--------------------------------------------------|
| `--download`      | Download bibliography from Malpedia              |
| `-d, --database`  | Database path (default: malpedia.db)              |
| `-s, --scrape`    | Scrape URLs after importing                      |
| `--scrape-only`   | Skip import, only scrape                         |
| `-l, --limit`     | Limit number of URLs to scrape                   |
| `--start`         | Start scraping from this index                   |
| `-r, --raw-dir`   | Directory for saved HTML files (default: raw)    |


## Output

- `malpedia.db` - SQLite database with entries and scraped content
- `raw/` - Directory containing saved HTML files
