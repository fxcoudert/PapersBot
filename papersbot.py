#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# PapersBot
#
# purpose:  read journal RSS feeds and tweet selected entries
# license:  MIT License
# author:   François-Xavier Coudert
# e-mail:   fxcoudert@gmail.com
#

import os
import random
import re
import sys
import time
import urllib
import yaml

import atproto
import bs4
import feedparser
import filetype
import tweepy
from mastodon import Mastodon, MastodonError


# This is the regular expression that selects the papers of interest
regex = re.compile(r"""
  (   \b(MOF|MOFs|COF|COFs|ZIF|ZIFs)\b
    | metal.organic.framework
    | covalent.organic.framework
    | metal–organic.framework
    | covalent–organic.framework
    | imidazolate.framework
    | porous.coordination.polymer
    | framework.material
  )
  """, re.IGNORECASE | re.VERBOSE)


# We select entries based on title or summary (abstract, for some feeds)
def entryMatches(entry):
    # Malformed entry
    if "title" not in entry:
        return False

    if regex.search(entry.title):
        return True
    if "summary" in entry:
        return regex.search(entry.summary)
    else:
        return False


# Find the URL for an image associated with the entry
def findImage(entry):
    if "description" not in entry:
        return

    soup = bs4.BeautifulSoup(entry.description, "html.parser")
    img = soup.find("img")
    if img:
        img = img["src"]
        if len(img) == 0:
            return
        # If address is relative, append root URL
        if img[0] == "/":
            p = urllib.parse.urlparse(entry.id)
            img = f"{p.scheme}://{p.netloc}" + img

    return img


# Convert string from HTML to plain text
def htmlToText(s):
    return bs4.BeautifulSoup(s, "html.parser").get_text()


def downloadImage(url):
    if not url:
        return None

    try:
        img, _ = urllib.request.urlretrieve(url)
    except Exception:
        return None
    kind = filetype.guess(img)
    if kind:
        # Rename to make type clear
        res = f"{img}.{kind.extension}"
        os.rename(img, res)
    else:
        # Not an image
        return None

    # Images smaller than 4 KB have a problem, and Twitter will complain
    if os.path.getsize(res) < 4096:
        os.remove(res)
        return None

    return res


# Helper functions for Bluesky, adapted from
# https://github.com/MarshalX/atproto/blob/main/examples/advanced_usage/auto_hyperlinks.py

def bluesky_extract_url_byte_positions(text, *, aggressive: bool, encoding='UTF-8'):
    """
    If aggressive is False, only links beginning http or https will be detected
    """
    encoded_text = text.encode(encoding)

    if aggressive:
        pattern = rb'(?:[\w+]+\:\/\/)?(?:[\w\d-]+\.)*[\w-]+[\.\:]\w+\/?(?:[\/\?\=\&\#\.\(\)]?[\w-]+)+\/?'
    else:
        pattern = rb'https?\:\/\/(?:[\w\d-]+\.)*[\w-]+[\.\:]\w+\/?(?:[\/\?\=\&\#\.\(\)]?[\w-]+)+\/?'

    matches = re.finditer(pattern, encoded_text)
    url_byte_positions = []
    for match in matches:
        url_bytes = match.group(0)
        url = url_bytes.decode(encoding)
        url_byte_positions.append((url, match.start(), match.end()))

    return url_byte_positions


def bluesky_post_with_links(client, text, image_file):
    """
    Send a skeet, identifying and handling links
    """
    # Determine locations of URLs in the post's text
    url_positions = bluesky_extract_url_byte_positions(text, aggressive=False)
    facets = []

    if image_file:
        with open(image_file, 'rb') as f:
            img_data = f.read()
        upload = client.com.atproto.repo.upload_blob(img_data)
        images = [atproto.models.AppBskyEmbedImages.Image(alt="TOC Graphic", image=upload.blob)]
        embed = atproto.models.AppBskyEmbedImages.Main(images=images)
    else:
        embed = None

    # AT requires URL to include http or https when creating the facet. Appends to URL if not present
    for link in url_positions:
        uri = link[0] if link[0].startswith('http') else f'https://{link[0]}'
        facets.append(
            atproto.models.AppBskyRichtextFacet.Main(
                features=[atproto.models.AppBskyRichtextFacet.Link(uri=uri)],
                index=atproto.models.AppBskyRichtextFacet.ByteSlice(byte_start=link[1], byte_end=link[2]),
            )
        )

    client.com.atproto.repo.create_record(
        atproto.models.ComAtprotoRepoCreateRecord.Data(
            repo=client.me.did,
            collection=atproto.models.ids.AppBskyFeedPost,
            record=atproto.models.AppBskyFeedPost.Record(created_at=client.get_current_time_iso(), text=text, facets=facets, embed=embed),
        )
    )


# Connect to Twitter and authenticate
#   Credentials are passed in the environment,
#   or stored in "credentials.yml" which contains four lines:
#   CONSUMER_KEY: "x1F3s..."
#   CONSUMER_SECRET: "3VNg..."
#   ACCESS_KEY: "7109..."
#   ACCESS_SECRET: "AdnA..."
#
def initTwitter():
    if 'CONSUMER_KEY' in os.environ:
        cred = {'CONSUMER_KEY': os.environ['CONSUMER_KEY'],
                'CONSUMER_SECRET': os.environ['CONSUMER_SECRET'],
                'ACCESS_KEY': os.environ['ACCESS_KEY'],
                'ACCESS_SECRET': os.environ['ACCESS_SECRET']}
    else:
        with open("credentials.yml", "r") as f:
            cred = yaml.safe_load(f)

    # v1 API
    auth = tweepy.OAuthHandler(cred["CONSUMER_KEY"], cred["CONSUMER_SECRET"])
    auth.set_access_token(cred["ACCESS_KEY"], cred["ACCESS_SECRET"])
    v1 = tweepy.API(auth)

    # v2 API
    v2 = tweepy.Client(consumer_key=cred["CONSUMER_KEY"],
                       consumer_secret=cred["CONSUMER_SECRET"],
                       access_token=cred["ACCESS_KEY"],
                       access_token_secret=cred["ACCESS_SECRET"])

    print("Twitter authentification worked")
    return v1, v2


# Connect to Mastodon
#   Credentials are passed in the environment,
#   or stored in "mastodon_credentials.yml" which contains five lines:
# MASTODON_API_BASE_URL: "https://mstdn.science"
# MASTODON_CLIENT_ID: "xxx"
# MASTODON_CLIENT_SECRET: "xxx"
# MASTODON_USER: "xxx@xxx.com"
# MASTODON_PASSWORD: "xxx"
#
def initMastodon():
    if 'MASTODON_API_BASE_URL' in os.environ:
        cred = {'API_BASE_URL': os.environ['MASTODON_API_BASE_URL'],
                'CLIENT_ID': os.environ['MASTODON_CLIENT_ID'],
                'CLIENT_SECRET': os.environ['MASTODON_CLIENT_SECRET'],
                'USER': os.environ['MASTODON_USER'],
                'PASSWORD': os.environ['MASTODON_PASSWORD']}
    else:
        with open("mastodon_credentials.yml", "r") as f:
            cred = yaml.safe_load(f)

    mastodon = Mastodon(client_id=cred["CLIENT_ID"], client_secret=cred["CLIENT_SECRET"], api_base_url=cred["API_BASE_URL"])
    token = mastodon.log_in(cred["USER"], cred["PASSWORD"])
    mastodon = Mastodon(access_token=token, api_base_url=cred["API_BASE_URL"])

    print("Mastodon authentification worked")
    return mastodon


# Connect to Bluesky
#   Credentials are passed in the environment,
#   or stored in "bluesky_credentials.yml" which contains two lines:
# BLUESKY_HANDLE: "xxx.bsky.social"
# BLUESKY_APP_PASSWORD: "xxx"
#
def initBluesky():
    if 'BLUESKY_HANDLE' in os.environ:
        cred = {'HANDLE': os.environ['BLUESKY_HANDLE'],
                'APP_PASSWORD': os.environ['BLUESKY_APP_PASSWORD']}
    else:
        with open("bluesky_credentials.yml", "r") as f:
            cred = yaml.safe_load(f)

    bluesky = atproto.Client()
    bluesky.login(cred['HANDLE'], cred['APP_PASSWORD'])

    print("Bluesky authentification worked")
    return bluesky


# Read our list of feeds from file
def readFeedsList():
    with open("feeds.txt", "r") as f:
        feeds = [s.partition("#")[0].strip() for s in f]
        return [s for s in feeds if s]


# Remove unwanted text some journals insert into the feeds
def cleanText(s):
    # Annoying ASAP tags
    s = s.replace("[ASAP]", "")
    # Some feeds have LF characters
    s = s.replace("\x0A", "")
    # Remove (arXiv:1903.00279v1 [cond-mat.mtrl-sci])
    s = re.sub(r"\(arXiv:.+\)", "", s)
    # Remove multiple spaces, leading and trailing space
    return re.sub("\\s\\s+", " ", s).strip()


# Read list of feed items already posted
def readPosted():
    try:
        with open("posted.dat", "r") as f:
            return f.read().splitlines()
    except OSError:
        return []


class PapersBot:
    posted = []
    n_seen = 0
    n_tweeted = 0

    def __init__(self, doTweet=True):
        self.feeds = readFeedsList()
        self.posted = readPosted()

        # Read parameters from configuration file
        try:
            with open("config.yml", "r") as f:
                config = yaml.safe_load(f)
        except OSError:
            config = {}
        self.throttle = config.get("throttle", 0)
        self.wait_time = config.get("wait_time", 5)
        self.shuffle_feeds = config.get("shuffle_feeds", True)
        self.blacklist = config.get("blacklist", [])
        self.blacklist = [re.compile(s) for s in self.blacklist]

        # Shuffle feeds list
        if self.shuffle_feeds:
            random.shuffle(self.feeds)

        # Connect to Twitter, unless requested not to
        if doTweet:
            self.api_v1, self.api_v2 = initTwitter()
            # Try to connect to Bluesky
            try:
                self.bluesky = initBluesky()
            except Exception:
                print('Did not connect to Bluesky')
                self.bluesky = None
            # Try to connect to Mastodon
            try:
                self.mastodon = initMastodon()
            except Exception:
                print('Did not connect to Mastodon')
                self.mastodon = None
        else:
            self.api_v1 = None
            self.api_v2 = None
            self.bluesky = None
            self.mastodon = None

        # Maximum shortened URL length (previously short_url_length_https)
        urllen = 23
        # Maximum URL length for media (previously characters_reserved_per_media)
        imglen = 24
        # Determine maximum tweet length
        self.maxlength = 280 - (urllen + 1) - imglen

        # Start-up banner
        print(f"This is PapersBot running at {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"Feed list has {len(self.feeds)} feeds\n")

    # Add to tweets posted
    def addToPosted(self, url):
        with open("posted.dat", "a+") as f:
            print(url, file=f)
        self.posted.append(url)

    # Send a tweet for a given feed entry
    def sendTweet(self, entry):
        title = cleanText(htmlToText(entry.title))
        length = self.maxlength

        # Usually the ID is the canonical URL, but not always
        if entry.id[:8] == "https://" or entry.id[:7] == "http://":
            url = entry.id
        else:
            url = entry.link

        # URL may be malformed
        if not (url[:8] == "https://" or url[:7] == "http://"):
            print(f"INVALID URL: {url}\n")
            return

        tweet_body = title[:length] + " " + url

        # URL may match our blacklist
        for regexp in self.blacklist:
            if regexp.search(url):
                print(f"BLACKLISTED: {tweet_body}\n")
                self.addToPosted(entry.id)
                return

        media = None
        mastodon_media = None
        image = findImage(entry)
        image_file = downloadImage(image)
        if image_file:
            print(f"IMAGE: {image}")
            if self.api_v1:
                media = [self.api_v1.media_upload(image_file).media_id]
            if self.bluesky:
                # For Bluesky, this is handled below
                pass
            if self.mastodon:
                mastodon_media = [self.mastodon.media_post(image_file)]

        print(f"TWEET: {tweet_body}\n")
        if self.api_v2:
            try:
                self.api_v2.create_tweet(text=tweet_body, media_ids=media)
            except tweepy.errors.TooManyRequests as e:
                print("ERROR: Too many requests, rate limit hit. Stopping now.\n")
                sys.exit(1)
            except tweepy.errors.TweepyException as e:
                if 187 in e.api_codes:
                    print("ERROR: Tweet refused as duplicate\n")
                else:
                    print(f"ERROR: Tweet refused, {e.reason}\n")
                    sys.exit(1)
        if self.bluesky:
            try:
                # Simple method, but does not include links as links
                # self.bluesky.send_post(text=tweet_body)
                # Smarter way:
                bluesky_post_with_links(self.bluesky, tweet_body, image_file)
            except Exception as e:
                print(f"ERROR: Bluesky post refused: {e}\n")
                sys.exit(1)
        if self.mastodon:
            try:
                self.mastodon.status_post(tweet_body, media_ids=mastodon_media)
            except MastodonError as e:
                print(f"ERROR: Toot refused: {e}\n")
                sys.exit(1)

        self.addToPosted(entry.id)
        self.n_tweeted += 1

        if image_file:
            os.remove(image_file)

        if self.api_v2 or self.mastodon:
            time.sleep(self.wait_time)

    # Main function, iterating over feeds and posting new items
    def run(self):
        for feed in self.feeds:
            try:
                parsed_feed = feedparser.parse(feed)
            except ConnectionResetError as e:
                # Print information about which feed is failing, and what is the error
                print("Failure to load feed at URL", feed)
                print("Exception info:", str(e))
                sys.exit(1)

            for entry in parsed_feed.entries:
                if entryMatches(entry):
                    self.n_seen += 1
                    # If no ID provided, use the link as ID
                    if "id" not in entry:
                        entry.id = entry.link
                    if entry.id not in self.posted:
                        self.sendTweet(entry)
                        # Bail out if we have reached max number of tweets
                        if self.throttle > 0 and self.n_tweeted >= self.throttle:
                            print(f"Max number of papers met ({self.throttle}), stopping now")
                            return

    # Print statistics of a given run
    def printStats(self):
        print(f"Number of relevant papers: {self.n_seen}")
        print(f"Number of papers tweeted: {self.n_tweeted}")

    # Print out the n top tweets (most liked and RT'ed)
    def printTopTweets(self, count=20):
        tweets = self.api_v1.user_timeline(count=200)
        oldest = tweets[-1].created_at
        print(f"Top {count} recent tweets, by number of RT and likes, since {oldest}:\n")

        tweets = [(t.retweet_count + t.favorite_count, t.id, t) for t in tweets]
        tweets.sort(reverse=True)
        for _, _, t in tweets[0:count]:
            url = f"https://twitter.com/{t.user.screen_name}/status/{t.id}"
            print(f"{t.retweet_count} RT {t.favorite_count} likes: {url}")
            print(f"    {t.created_at}")
            print(f"    {t.text}\n")


def main():
    # Make sure all options are correctly typed
    options_allowed = ["--do-not-tweet", "--top-tweets"]
    for arg in sys.argv[1:]:
        if arg not in options_allowed:
            print(f"Unknown option: {arg}")
            sys.exit(1)

    # Initialize our bot
    doTweet = "--do-not-tweet" not in sys.argv
    bot = PapersBot(doTweet)

    # We can print top tweets
    if "--top-tweets" in sys.argv:
        bot.printTopTweets()
        sys.exit(0)

    bot.run()
    bot.printStats()


if __name__ == "__main__":
    main()
