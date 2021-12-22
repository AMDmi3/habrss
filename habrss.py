#!/usr/bin/env python3
#
# Copyright (C) 2021 Dmitry Marakasov <amdmi3@amdmi3.ru>
#
# This file is part of habrss
#
# habrss is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# habrss is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with habrss.  If not, see <http://www.gnu.org/licenses/>.


import argparse
import asyncio
import itertools
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import field
from typing import TYPE_CHECKING, Iterable, Iterator, Optional

import aiohttp
import aiohttp.web
import jinja2
import yaml

if TYPE_CHECKING:
    from dataclasses import dataclass
else:
    from pydantic.dataclasses import dataclass


@dataclass
class FilterConfig:
    title: Optional[str] = None
    category: Optional[str] = None
    creator: Optional[str] = None


@dataclass
class FeedConfig:
    name: str
    urls: list[str]
    exclude: list[FilterConfig] = field(default_factory=list)
    include: list[FilterConfig] = field(default_factory=list)


@dataclass
class FeedsConfig:
    feeds: list[FeedConfig]


@dataclass
class FeedItem:
    title: str
    guid: str
    guid_permalink: str
    link: str
    description: str
    pub_date: str
    categories: list[str]
    creator: str

    @staticmethod
    def unicalize(items: Iterable['FeedItem']) -> Iterator['FeedItem']:
        seen_guids = set()

        for item in items:
            if item.guid not in seen_guids:
                seen_guids.add(item.guid)
                yield item

    def __repr__(self) -> str:
        res = f'"{self.title}" by "{self.creator}"'

        if categories := ', '.join(f'"{cat}"' for cat in self.categories):
            res += f' ({categories})'

        res += f' - <{self.link}>'

        return res


@dataclass
class FilterStatistics:
    passed: dict[str, FeedItem] = field(default_factory=dict)
    blocked: dict[str, FeedItem] = field(default_factory=dict)

    def add(self, item: FeedItem, passed: bool) -> None:
        target = self.passed if passed else self.blocked
        other = self.passed if not passed else self.blocked

        key = item.title

        if key in other:
            del other[key]

        target[key] = item

    @property
    def passed_categories(self) -> list[tuple[int, str]]:
        return sorted(
            (
                (count, key)
                for key, count in Counter(
                    category
                    for item in self.passed.values()
                    for category in item.categories
                ).items()
            ),
            reverse=True
        )

    @property
    def blocked_categories(self) -> list[tuple[int, str]]:
        return sorted(
            (
                (count, key)
                for key, count in Counter(
                    category
                    for item in self.blocked.values()
                    for category in item.categories
                ).items()
            ),
            reverse=True
        )

    @property
    def passed_creators(self) -> list[tuple[int, str]]:
        return sorted(
            (
                (count, key)
                for key, count in Counter(
                    item.creator
                    for item in self.passed.values()
                ).items()
            ),
            reverse=True
        )

    @property
    def blocked_creators(self) -> list[tuple[int, str]]:
        return sorted(
            (
                (count, key)
                for key, count in Counter(
                    item.creator
                    for item in self.blocked.values()
                ).items()
            ),
            reverse=True
        )


def cleanup_link(link: str) -> str:
    return link.split('?utm')[0]


def parse_feed(content: str) -> Iterator[FeedItem]:
    root = ET.fromstring(content)

    for item in root.findall('channel/item'):
        yield FeedItem(
            title=item.find('title').text,  # type: ignore
            guid=item.find('guid').text,  # type: ignore
            guid_permalink=item.find('guid').attrib.get('isPermaLink'),  # type: ignore
            link=cleanup_link(item.find('link').text),  # type: ignore
            description=item.find('description').text,  # type: ignore
            pub_date=item.find('pubDate').text,  # type: ignore
            categories=[elt.text for elt in item.findall('category')],  # type: ignore
            creator=item.find('{http://purl.org/dc/elements/1.1/}creator').text,  # type: ignore
        )


def check_filters(item: FeedItem, filters: Iterable[FilterConfig]) -> bool:
    for filt in filters:
        if filt.title is not None and re.fullmatch(filt.title, item.title, re.IGNORECASE):
            return True
        if filt.category is not None and any(re.fullmatch(filt.category, category, re.IGNORECASE) for category in item.categories):
            return True
        if filt.creator is not None and re.fullmatch(filt.creator, item.creator, re.IGNORECASE):
            return True
    return False


def process_feed_items(items: Iterable[FeedItem], feed_config: FeedConfig, stats: FilterStatistics) -> Iterator[FeedItem]:
    for item in FeedItem.unicalize(items):
        if check_filters(item, feed_config.exclude) and not check_filters(item, feed_config.include):
            stats.add(item, False)
        else:
            stats.add(item, True)
            yield item


def dump_feed(items: Iterable[FeedItem]) -> str:
    root = ET.Element('rss')

    ET.SubElement(root, 'title').text = 'habrss feed'

    channel = ET.SubElement(root, 'channel')

    for item in items:
        item_elt = ET.SubElement(channel, 'item')

        ET.SubElement(item_elt, 'title').text = item.title
        guid_elt = ET.SubElement(item_elt, 'guid')
        guid_elt.text = item.guid
        if item.guid_permalink:
            guid_elt.attrib['isPermaLink'] = item.guid_permalink
        ET.SubElement(item_elt, 'link').text = item.link
        ET.SubElement(item_elt, 'description').text = item.description
        ET.SubElement(item_elt, 'pubDate').text = item.pub_date

        for category in item.categories:
            ET.SubElement(item_elt, 'category').text = category

        ET.SubElement(item_elt, '{http://purl.org/dc/elements/1.1/}creator').text = item.creator

    return ET.tostring(root, encoding='unicode')


class Handler:
    _config: FeedsConfig
    _stats: FilterStatistics

    _index_template: jinja2.Template
    _stats_template: jinja2.Template

    def __init__(self, config: FeedsConfig) -> None:
        self._config = config
        self._stats = FilterStatistics()
        self._index_template = jinja2.Template(
            """
            <html>
            <head><title>Feeds list</title></head>
            <body>
            <h1>Feeds list</h1>
            <ul>
            {% for feed in feeds %}
            <li><a href="{{ feed.name }}.rss">{{ feed.name }}</a></li>
            {% endfor %}
            </ul>
            <p><a href="stats">Filter statistics</a></p>
            </body>
            </html>
            """
        )
        self._stats_template = jinja2.Template(
            """
            <html>
            <head><title>Filter statistics</title></head>
            <body><h1>Filter statistics</h1>

            <table>
            <tr><th>Title</th><th>Creator</th><th>Categories</th></tr>
            <tr><td colspan="3"><h3>Blocked</h3></td></tr>
            {% for _, item in stats.blocked.items()|sort %}
            <tr>
            <td><a href="{{ item.link }}">{{ item.title }}</a></td>
            <td>{{ item.creator }}</td>
            <td>{{ item.categories | join(', ') }}</td>
            </tr>
            {% endfor %}
            <tr><td colspan="3"><h3>Passed</h3></td></tr>
            {% for _, item in stats.passed.items()|sort %}
            <tr>
            <td><a href="{{ item.link }}">{{ item.title }}</a></td>
            <td>{{ item.creator }}</td>
            <td>{{ item.categories | join(', ') }}</td>
            </tr>
            {% endfor %}
            </table>

            <h3>Blocked categories</h3>
            <table>
            <tr><th>Category</th><th>Count</th></tr>
            {% for cat, count in stats.blocked_categories %}
            <tr><td>{{ cat }}</td><td>{{ count }}</td></tr>
            {% endfor %}
            </table>

            <h3>Passed categories</h3>
            <table>
            <tr><th>Category</th><th>Count</th></tr>
            {% for cat, count in stats.passed_categories %}
            <tr><td>{{ cat }}</td><td>{{ count }}</td></tr>
            {% endfor %}
            </table>

            <h3>Blocked creators</h3>
            <table>
            <tr><th>Category</th><th>Count</th></tr>
            {% for creator, count in stats.blocked_creators %}
            <tr><td>{{ creator }}</td><td>{{ count }}</td></tr>
            {% endfor %}
            </table>

            <h3>Passed creators</h3>
            <table>
            <tr><th>Category</th><th>Count</th></tr>
            {% for creator, count in stats.passed_creators %}
            <tr><td>{{ creator }}</td><td>{{ count }}</td></tr>
            {% endfor %}
            </table>

            </body>
            </html>
            """
        )

    async def handle_index(self, request):
        return aiohttp.web.Response(
            text=self._index_template.render(feeds=self._config.feeds),
            content_type='text/html'
        )

    async def handle_feed(self, request):
        feed_name = request.match_info['name']
        for feed_config in self._config.feeds:
            if feed_config.name == feed_name:
                break
        else:
            raise aiohttp.web.HTTPNotFound()

        async with aiohttp.ClientSession() as session:
            tasks = [
                session.get(
                    url,
                    headers={
                        'user-agent': request.headers['user-agent']
                    }
                ) for url in feed_config.urls
            ]

            responses = await asyncio.gather(*tasks)

            content_type = responses[0].headers['content-type'].split(';', 1)[0]

            contents = [await response.text() for response in responses]

            result = '<?xml version="1.0" encoding="UTF-8"?>\n' + dump_feed(
                process_feed_items(
                    itertools.chain(*map(parse_feed, contents)),
                    feed_config,
                    self._stats,
                )
            )

            return aiohttp.web.Response(text=result, content_type=content_type)

    async def handle_stats(self, request):
        return aiohttp.web.Response(
            text=self._stats_template.render(stats=self._stats),
            content_type='text/html'
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-f', '--config', type=str, required=True, help='path to config file')
    parser.add_argument('-l', '--host', type=str, default='127.0.0.1', help='host to listen on')
    parser.add_argument('-p', '--port', type=int, default=8080, help='port to listen on')
    return parser.parse_args()


def load_config(path: str) -> FeedsConfig:
    with open(path) as fd:
        return FeedsConfig(yaml.safe_load(fd))


def main():
    args = parse_args()

    config = load_config(args.config)

    handler = Handler(config)

    app = aiohttp.web.Application()
    app.add_routes([
        aiohttp.web.get('/', handler.handle_index),
        aiohttp.web.get('/{name}.rss', handler.handle_feed),
        aiohttp.web.get('/stats', handler.handle_stats),
    ])
    aiohttp.web.run_app(app, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
