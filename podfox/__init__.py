#!/usr/bin/python3
"""podfox - podcatcher for the terminal


Usage:
    podfox.py import <feed-url> [<shortname>] [-c=<path>]
    podfox.py update [<shortname>] [-c=<path>]
    podfox.py feeds [-c=<path>]
    podfox.py episodes <shortname> [-c=<path>]
    podfox.py download [<shortname> --how-many=<n>] [-c=<path>]
    podfox.py rename <shortname> <newname> [-c=<path>]
    podfox.py delete <shortname> [-c=<path>]

Options:
    -c --config=<path>    Specify an alternate config file [default: ~/.podfox.json]
    -h --help     Show this help
"""
# (C) 2015 Bastian Reitemeier
# mail(at)brtmr.de

from colorama import Fore, Back, Style
from docopt import docopt
from os.path import expanduser
from sys import exit
import colorama
import feedparser
import json
import os
import os.path
import requests
import sys
import re
import shutil

# RSS datetimes follow RFC 2822, same as email headers.
# this is the chain of stackoverflow posts that led me to believe this is true.
# http://stackoverflow.com/questions/11993258/
# what-is-the-correct-format-for-rss-feed-pubdate
# http://stackoverflow.com/questions/885015/
# how-to-parse-a-rfc-2822-date-time-into-a-python-datetime

from email.utils import parsedate
from time import mktime

CONFIGURATION_DEFAULTS = {
    "podcast-directory": "~/Podcasts",
    "maxnum": 5000,
    "mimetypes": [ "audio/aac",
                   "audio/ogg",
                   "audio/mpeg",
                   "audio/mp3",
                   "audio/mp4",
                   "video/mp4" ]
}

mimetypes = [
    'audio/ogg',
    'audio/mpeg',
    'video/mp4',
    'audio/x-m4a'
]

def print_err(err):
    print(Fore.RED + Style.BRIGHT + err +
          Fore.RESET + Back.RESET + Style.RESET_ALL, file=sys.stderr)


def print_green(s):
    print(Fore.GREEN + s + Fore.RESET)


def sort_feed(feed):
    feed['episodes'] = sorted(feed['episodes'], key=lambda k: k['published'],
                              reverse=True)
    return feed


class PodFox(object):
    def __init__(self):
        self.CONFIGURATION = {}

    def get_folder(self, shortname):
        base = self.CONFIGURATION['podcast-directory']
        return os.path.join(base, shortname)

    def get_feed_file(self, shortname):
        return os.path.join(self.get_folder(shortname), 'feed.json')

    def import_feed(self, url, shortname=''):
        '''
        creates a folder for the new feed, and then inserts a new feed.json
        that will contain all the necessary information about this feed, and
        all the episodes contained.
        '''
        # configuration for this feed, will be written to file.
        feed = {}
        #get the feed.
        d = feedparser.parse(url)

        if shortname:
            folder = self.get_folder(shortname)
            if os.path.exists(folder):
                print_err(
                    '{} already exists'.format(folder))
                exit(-1)
            else:
                os.makedirs(folder)
        #if the user did not specify a folder name,
        #we have to create one from the title
        if not shortname:
            # the rss advertises a title, lets use that.
            if hasattr(d['feed'], 'title'):
                title = d['feed']['title']
            # still no succes, lets use the last part of the url
            else:
                title = url.rsplit('/', 1)[-1]
            # we wanna avoid any filename crazyness,
            # so foldernames will be restricted to lowercase ascii letters,
            # numbers, and dashes:
            title = ''.join(ch for ch in title
                    if ch.isalnum() or ch == ' ')
            shortname = title.replace(' ', '-').lower()
            if not shortname:
                print_err('could not auto-deduce shortname.')
                print_err('please provide one explicitly.')
                exit(-1)
            folder = self.get_folder(shortname)
            if os.path.exists(folder):
                print_err(
                    '{} already exists'.format(folder))
                exit(-1)
            else:
                os.makedirs(folder)
        #we have succesfully generated a folder that we can store the files
        #in
        #trawl all the entries, and find links to audio files.
        feed['episodes'] = self.episodes_from_feed(d)
        feed['shortname'] = shortname
        feed['title'] = d['feed']['title']
        feed['url'] = url
        # write the configuration to a feed.json within the folder
        feed_file = self.get_feed_file(shortname)
        feed = sort_feed(feed)
        with open(feed_file, 'x') as f:
            json.dump(feed, f, indent=4)
        print('imported ' +
            Fore.GREEN + feed['title'] + Fore.RESET + ' with shortname ' +
            Fore.BLUE + feed['shortname'] + Fore.RESET)

    def delete_feed(self, feed):
        folder = self.get_folder(feed['shortname'])
        if os.path.exists(folder):
            shutil.rmtree(folder)

    def update_feed(self, feed):
        '''
        download the current feed, and insert previously unknown
        episodes into our local config.
        '''
        d = feedparser.parse(feed['url'])
        #only append new episodes!
        for episode in self.episodes_from_feed(d):
            found = False
            for old_episode in feed['episodes']:
                if episode['published'] == old_episode['published'] \
                        and episode['title'] == old_episode['title']:
                    found = True
            if not found:
                feed['episodes'].append(episode)
                print('new episode.')
        feed = sort_feed(feed)
        self.overwrite_config(feed)

    def overwrite_config(self, feed):
        '''
        after updating the feed, or downloading new items,
        we want to update our local config to reflect that fact.
        '''
        filename = self.get_feed_file(feed['shortname'])
        with open(filename, 'w') as f:
            json.dump(feed, f, indent=4)

    def episodes_from_feed(self, d):
        mimetypes = self.CONFIGURATION['mimetypes']

        episodes = []
        for entry in d.entries:
            # convert publishing time to unix time, so that we can sort
            # this should be unix time, barring any timezone shenanigans
            date = mktime(parsedate(entry.published))
            if hasattr(entry, 'links'):
                for link in entry.links:
                    if not hasattr(link, 'type'):
                        continue
                    if hasattr(link, 'type') and (link.type in mimetypes):
                        if hasattr(entry, 'title'):
                            episode_title = entry.title
                        else:
                            episode_title = link.href
                        episodes.append({
                            'title':      episode_title,
                            'url':        link.href,
                            'downloaded': False,
                            'listened':   False,
                            'published':  date,
                            'filename':   None,
                            })
        return episodes

    def download_multiple(self, feed, maxnum=None):
        if not maxnum:
            maxnum = self.CONFIGURATION['maxnum']

        for episode in feed['episodes']:
            if maxnum == 0:
                break
            if not episode['downloaded']:
                filename = self.download_single(feed['shortname'], episode['url'])
                episode['downloaded'] = True
                episode['filename'] = filename
                maxnum -= 1
        self.overwrite_config(feed)

    def download_single(self, folder, url):
        print(url)
        base = self.CONFIGURATION['podcast-directory']
        r = requests.get(url.strip(), stream=True)
        try:
            filename=re.findall('filename="([^"]+)',r.headers['content-disposition'])[0]
        except:
            filename = url.split('/')[-1]
            filename = filename.split('?')[0]
        print_green("{:s} downloading".format(filename))
        with open(os.path.join(base, folder, filename), 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024**2):
                f.write(chunk)
        print("done.")
        return filename

    def available_feeds(self):
        '''
        podfox will save each feed to its own folder. Each folder should
        contain a json configuration file describing which elements
        have been downloaded already, and how many will be kept.
        '''
        base = self.CONFIGURATION['podcast-directory']
        paths = [p for p in os.listdir(base)
                if os.path.isdir(self.get_folder(p))
                and os.path.isfile(self.get_feed_file(p))]
        #for every folder, check wether a configuration file exists.
        results = []
        for shortname in paths:
            with open(self.get_feed_file(shortname), 'r') as f:
                feed = json.load(f)
                results.append(feed)
        return sorted(results, key=lambda k: k['title'])

    def find_feed(self, shortname):
        '''
        all feeds are identified by their shortname, which is also the name of
        the folder they will be stored in.
        this function will find the correct folder, and parse the json file
        within that folder to generate the feed data
        '''
        feeds = self.available_feeds()
        for feed in feeds:
            if feed['shortname'] == shortname:
                return feed
        return None

    def rename(self, shortname, newname):
        folder = self.get_folder(shortname)
        new_folder = self.get_folder(newname)
        if not os.path.isdir(folder):
            print_err('folder {0} not found'.format(folder))
            exit(-1)
        os.rename(folder, new_folder)
        feed = self.find_feed(shortname)
        feed['shortname'] = newname
        self.overwrite_config(feed)

    def pretty_print_feeds(self, feeds):
        format_str = Fore.GREEN + '{0:45.45} |'
        format_str += Fore.BLUE + '  {1:40}' + Fore.RESET + Back.RESET
        print(format_str.format('title', 'shortname'))
        print('='*80)
        for feed in feeds:
            format_str = Fore.GREEN + '{0:40.40} {1:3d}{2:1.1} |'
            format_str += Fore.BLUE + '  {3:40}' + Fore.RESET + Back.RESET
            feed = sort_feed(feed)
            amount = len([ep for ep in feed['episodes'] if ep['downloaded']])
            dl = '' if feed['episodes'][0]['downloaded'] else '*'
            print(format_str.format(feed['title'], amount, dl, feed['shortname']))

    def pretty_print_episodes(self, feed):
        format_str = Fore.GREEN + '{0:40}  |'
        format_str += Fore.BLUE + '  {1:20}' + Fore.RESET + Back.RESET
        for e in feed['episodes'][:20]:
            status = 'Downloaded' if e['downloaded'] else 'Not Downloaded'
            print(format_str.format(e['title'][:40], status))

    def parse_config(self, config_path):
        configfile = expanduser(config_path)

        try:
            with open(configfile) as conf_file:
                try:
                    userconf = json.load(conf_file)
                except ValueError:
                    print("invalid json in configuration file.")
                    exit(-1)
        except FileNotFoundError:
            userconf = {}

        self.CONFIGURATION = CONFIGURATION_DEFAULTS.copy()
        self.CONFIGURATION.update(userconf)
        self.CONFIGURATION['podcast-directory'] = os.path.expanduser(self.CONFIGURATION['podcast-directory'])

    # TODO add a unique id for each episode so this is less brittle
    def mark_episode_listened(self, feed, episode_title):
        for episode in feed['episodes']:
            if episode['title'] == episode_title:
                episode['listened'] = True

                if 'filename' in episode and episode['filename']:
                    path = os.path.join(
                        self.CONFIGURATION['podcast-directory'],
                        feed['shortname'],
                        episode['filename'],
                    )
                    if os.path.exists(path):
                        os.unlink(path)

        self.overwrite_config(feed)


def main():
    colorama.init()
    arguments = docopt(__doc__, version='p0d 0.01')

    podfox = PodFox()
    podfox.parse_config(arguments["--config"])

    if arguments['import']:
        if arguments['<shortname>'] is None:
            podfox.import_feed(arguments['<feed-url>'])
        else:
            podfox.import_feed(arguments['<feed-url>'],
                        shortname=arguments['<shortname>'])
        exit(0)

    if arguments['feeds']:
        podfox.pretty_print_feeds(podfox.available_feeds())
        exit(0)

    if arguments['episodes']:
        feed = podfox.find_feed(arguments['<shortname>'])
        if feed:
            podfox.pretty_print_episodes(feed)
            exit(0)
        else:
            print_err("feed {} not found".format(arguments['<shortname>']))
            exit(-1)

    if arguments['update']:
        if arguments['<shortname>']:
            feed = podfox.find_feed(arguments['<shortname>'])
            if feed:
                print_green('updating {}'.format(feed['title']))
                podfox.update_feed(feed)
                exit(0)
            else:
                print_err("feed {} not found".format(arguments['<shortname>']))
                exit(-1)
        else:
            for feed in podfox.available_feeds():
                print_green('updating {}'.format(feed['title']))
                podfox.update_feed(feed)
            exit(0)

    if arguments['download']:
        maxnum = None
        if arguments['--how-many']:
            maxnum = int(arguments['--how-many'])

        #download episodes for a specific feed
        if arguments['<shortname>']:
            feed = podfox.find_feed(arguments['<shortname>'])
            if feed:
                podfox.download_multiple(feed, maxnum)
                exit(0)
            else:
                print_err("feed {} not found".format(arguments['<shortname>']))
                exit(-1)
        #download episodes for all feeds.
        else:
            for feed in podfox.available_feeds():
                podfox.download_multiple(feed, maxnum)
            exit(0)

    if arguments['rename']:
        podfox.rename(arguments['<shortname>'], arguments['<newname>'])

    if arguments['delete']:
        feed = podfox.find_feed(arguments['<shortname>'])
        if feed:
            podfox.delete_feed(feed)
            exit(0)
        else:
            print_err("feed {} not found".format(arguments['<shortname>']))
            exit(-1)


if __name__ == '__main__':
    main()
