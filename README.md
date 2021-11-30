# habrss

This is a simple proxy which merges and filters RSS feeds from
[habr.ru](https://habr.ru).

Motivation: habr/geektimes, which were useful sources of knowledge
once, are now clogged with low quality content and corporate blogs.
When reading a feed of articles degrades into skipping through the
garbage, automated solution comes in rescue. Apart from advanced
filtering, this project is capable of skipping duplicate posts when
being subscribed to multiple feeds.

## Usage

```
./habrss.py [-l host] [-p port] -f config
```

where

* `-l` specifies host to listen on (default 127.0.0.1, specify 0.0.0.0 to listen on all interfaces)
* `-p` specifies port to listen on (default 8080)
* `-f` specifies path to config file

The script serves HTTP on a given host/port. Available pages include
a list of configured feeds, feeds themselves and a statistics on
posts blocked and passed by filters.

## Config

Example:

```
- name: myfeed
  urls:
    - https://habr.com/ru/rss/flows/develop/all/?fl=ru
    - https://habr.com/ru/rss/flows/popsci/all/?fl=ru
  exclude:
    - title: .*iOS.*
    - category: Natural Language Processing
    - creator: ph_piter
  include:
    - title: .*python.*
    - category: Python
    - creator: Zelenyikot
- name: anotherfeed
  urls:
    ...
```

Each config entry specifies an output feed served by the script.
Each such feed may aggregate one or more (in which case duplicates
are removed) habr.ru feeds speficied as a set of `urls`. It's also
possible to specify `exclude` filters which match post title, author,
or a category with a case-insenesitive regexp (so if you want a
partial match, use e.g. `title: .*python.*`). `include` filters
override `exclude` filters.

## License

GPLv3 or later, see [COPYING](COPYING).
