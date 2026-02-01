#!/usr/bin/env python3

import re
import sqlite3
import argparse
import requests
from pathlib import Path


class BibEntry:
    def __init__(self, entry_type, key, fields=None):
        self.entry_type = entry_type
        self.key = key
        fields = fields or {}
        self.author = fields.get('author', '')
        self.title = fields.get('title', '')
        self.date = fields.get('date', '')
        self.organization = fields.get('organization', '')
        self.url = fields.get('url', '')
        self.language = fields.get('language', '')
        self.urldate = fields.get('urldate', '')


class BibParser:
    ENTRY_RE = re.compile(r'@(\w+)\{([^,]+),\s*(.*?)\n\}', re.DOTALL)
    FIELD_RE = re.compile(r'(\w+)\s*=\s*\{(.*?)\}(?:,|\s*$)', re.DOTALL)
    DOWNLOAD_URL = 'https://malpedia.caad.fkie.fraunhofer.de/library/download'

    def __init__(self, filepath='malpedia.bib'):
        self.filepath = filepath

    def download(self):
        print(f"Downloading from {self.DOWNLOAD_URL}...")
        resp = requests.get(self.DOWNLOAD_URL)
        resp.raise_for_status()
        Path(self.filepath).write_text(resp.text, encoding='utf-8')
        print(f"Saved to {self.filepath}")

    def parse(self):
        with open(self.filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        for match in self.ENTRY_RE.finditer(content):
            entry_type = match.group(1)
            key = match.group(2).strip()
            fields = self._extract_fields(match.group(3))
            yield BibEntry(entry_type, key, fields)

    def _extract_fields(self, fields_str):
        fields = {}
        for m in self.FIELD_RE.finditer(fields_str):
            name = m.group(1).lower()
            value = m.group(2).strip()
            if name == 'title':
                value = value.strip('{}')
            fields[name] = value
        return fields


class Database:
    def __init__(self, path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self._init_schema()

    def _init_schema(self):
        cur = self.conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_type TEXT,
                key TEXT UNIQUE,
                author TEXT,
                title TEXT,
                date TEXT,
                organization TEXT,
                url TEXT,
                language TEXT,
                urldate TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS scraped_content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER,
                url TEXT,
                status_code INTEGER,
                content_type TEXT,
                content TEXT,
                filepath TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entry_id) REFERENCES entries(id)
            )
        ''')
        try:
            cur.execute('ALTER TABLE scraped_content ADD COLUMN filepath TEXT')
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    def insert_entries(self, entries):
        cur = self.conn.cursor()
        count = 0
        for e in entries:
            try:
                cur.execute('''
                    INSERT OR IGNORE INTO entries
                    (entry_type, key, author, title, date, organization, url, language, urldate)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (e.entry_type, e.key, e.author, e.title, e.date,
                      e.organization, e.url, e.language, e.urldate))
                if cur.rowcount > 0:
                    count += 1
            except sqlite3.IntegrityError:
                pass
        self.conn.commit()
        return count

    def get_unscraped_entries(self, skip_existing=True):
        cur = self.conn.cursor()
        if skip_existing:
            cur.execute('''
                SELECT e.id, e.url, e.title, e.key FROM entries e
                LEFT JOIN scraped_content sc ON e.id = sc.entry_id
                WHERE e.url != '' AND sc.id IS NULL
            ''')
        else:
            cur.execute('SELECT id, url, title, key FROM entries WHERE url != ""')
        return cur.fetchall()

    def save_scraped(self, entry_id, url, status, content_type, content, filepath):
        cur = self.conn.cursor()
        cur.execute('''
            INSERT INTO scraped_content (entry_id, url, status_code, content_type, content, filepath)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (entry_id, url, status, content_type, content[:100000], filepath))

    def commit(self):
        self.conn.commit()

    def stats(self):
        cur = self.conn.cursor()
        cur.execute('SELECT COUNT(*) FROM entries')
        entries = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM scraped_content')
        scraped = cur.fetchone()[0]
        return entries, scraped

    def close(self):
        self.conn.close()


class Scraper:
    HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    INVALID_CHARS = '<>:"/\\|?*'

    def __init__(self, db, raw_dir):
        self.db = db
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def run(self, limit=None, start=0, skip_existing=True):
        rows = self.db.get_unscraped_entries(skip_existing)
        if start > 0:
            rows = rows[start:]
        if limit:
            rows = rows[:limit]

        total = len(rows)
        print(f"Scraping {total} URLs (starting from {start})...")

        for i, (entry_id, url, title, key) in enumerate(rows, 1):
            print(f"[{i}/{total}] Scraping: {url[:80]}...")
            status, ctype, content = self._fetch(url)
            fpath = self._save_file(title or key, content, ctype)
            self.db.save_scraped(entry_id, url, status, ctype, content, str(fpath) if fpath else None)
            if i % 10 == 0:
                self.db.commit()

        self.db.commit()
        print(f"Scraping complete. Processed {total} URLs.")
        print(f"Raw HTML files saved to: {self.raw_dir}")

    def _fetch(self, url, timeout=30):
        try:
            resp = requests.get(url, headers=self.HEADERS, timeout=timeout)
            ctype = resp.headers.get('Content-Type', '')
            if any(t in ctype for t in ['text', 'html', 'json']):
                return resp.status_code, ctype, resp.text
            return resp.status_code, ctype, f"[Binary content: {ctype}]"
        except requests.RequestException as e:
            return -1, 'error', str(e)

    def _save_file(self, name, content, ctype):
        if not any(t in ctype for t in ['html', 'text']):
            return None
        filename = self._sanitize(name) + '.html'
        fpath = self.raw_dir / filename
        try:
            fpath.write_text(content, encoding='utf-8')
            return fpath
        except Exception as e:
            print(f"  Warning: Could not save file {filename}: {e}")
            return None

    def _sanitize(self, name):
        for c in self.INVALID_CHARS:
            name = name.replace(c, '_')
        return name[:150].strip()


def main():
    ap = argparse.ArgumentParser(description='BibTeX to SQLite Database Tool')
    ap.add_argument('bibfile', nargs='?', default='malpedia.bib')
    ap.add_argument('-d', '--database', default='malpedia.db')
    ap.add_argument('-r', '--raw-dir', default='raw')
    ap.add_argument('-s', '--scrape', action='store_true')
    ap.add_argument('-l', '--limit', type=int)
    ap.add_argument('--start', type=int, default=0)
    ap.add_argument('--scrape-only', action='store_true')
    ap.add_argument('--download', action='store_true')
    args = ap.parse_args()

    db = Database(args.database)
    parser = BibParser(args.bibfile)

    if args.download:
        parser.download()

    if not args.scrape_only:
        print(f"Parsing {args.bibfile}...")
        entries = list(parser.parse())
        print(f"Found {len(entries)} entries")
        count = db.insert_entries(entries)
        print(f"Inserted {count} new entries into {args.database}")

    if args.scrape or args.scrape_only:
        scraper = Scraper(db, args.raw_dir)
        scraper.run(limit=args.limit, start=args.start)

    total_entries, total_scraped = db.stats()
    raw_dir = Path(args.raw_dir)
    html_count = len(list(raw_dir.glob('*.html'))) if raw_dir.exists() else 0

    print(f"\nDatabase summary:")
    print(f"  Total entries: {total_entries}")
    print(f"  Scraped URLs: {total_scraped}")
    print(f"  Raw HTML files: {html_count}")

    db.close()


if __name__ == '__main__':
    main()
