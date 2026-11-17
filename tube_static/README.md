# Mrh Tube client

A YouTube-style website built from a public REA browser observation of YouTube's home and search flows. It is an independent site, not YouTube's source code or service. `tube.py` supplies accounts, uploads, comments, likes, saved videos, and history.

## Run

Run `uvicorn tube:app --reload` from the repository root. For local HTTP, set `COOKIE_SECURE=0` first.

## Features

- Responsive feed, categories, search results, watch page, and related videos
- YouTube embed playback and a direct link to each original video
- Accounts, uploads, likes, watch later, viewing history, and comments stored on the server
- Keyboard shortcut `/` to focus search

The built-in video catalog is curated. Search covers that catalog and community uploads. The built-in thumbnails are bundled; YouTube embed playback needs an internet connection and depends on each uploader's embed settings. Community uploads play from the Railway service.
