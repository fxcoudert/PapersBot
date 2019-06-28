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

import imghdr
import json
import os
import re
import sys
import tempfile
import time
import urllib
import yaml

import bs4
import feedparser
import tweepy


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
    ext = imghdr.what(img)
    res = img + "." + ext
    os.rename(img, res)

    # Images smaller than 4 KB have a problem, and Twitter will complain
    if os.path.getsize(res) < 4096:
        os.remove(res)
        return None

    return res


# Connect to Twitter and authenticate
#   Credentials are stored in "credentials.yml" which contains four lines:
#   CONSUMER_KEY: "x1F3s..."
#   CONSUMER_SECRET: "3VNg..."
#   ACCESS_KEY: "7109..."
#   ACCESS_SECRET: "AdnA..."
#
def initTwitter():
    with open("credentials.yml", "r") as f:
        cred = yaml.safe_load(f)
    auth = tweepy.OAuthHandler(cred["CONSUMER_KEY"], cred["CONSUMER_SECRET"])
    auth.set_access_token(cred["ACCESS_KEY"], cred["ACCESS_SECRET"])
    return tweepy.API(auth)


def getTwitterConfig(api):
    # Check for cached configuration, no more than a day old
    if os.path.isfile("twitter_config.dat"):
        mtime = os.stat("twitter_config.dat").st_mtime
        if time.time() - mtime < 24 * 60 * 60:
            with open("twitter_config.dat", "r") as f:
                return json.load(f)

    # Otherwise, query the Twitter API and cache the result
    config = api.configuration()
    with open("twitter_config.dat", "w") as f:
        json.dump(config, f)
    return config


# Read our list of feeds from file
def readFeedsList():
    with open("feeds.txt", "r") as f:
        feeds = [s.partition("#")[0].strip() for s in f]
        return [s for s in feeds if s]


# Remove unwanted text some journals insert into the feeds
def cleanText(s):
    # Annoying ASAP tags
    s = s.replace("[ASAP]", "")
    # Some feeds have LF characeters
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
    except Exception:
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
        except Except:
            config = {}
        self.throttle = config.get("throttle", 0)
        self.wait_time = config.get("wait_time", 5)
        self.blacklist = config.get("blacklist", [])
        self.blacklist = [re.compile(s) for s in self.blacklist]

        # Connect to Twitter, unless requested not to
        if doTweet:
            self.api = initTwitter()
        else:
            self.api = None

        # Determine maximum tweet length
        if doTweet:
            twconfig = getTwitterConfig(self.api)
            urllen = max(twconfig["short_url_length"], twconfig["short_url_length_https"])
            imglen = twconfig["characters_reserved_per_media"]
        else:
            urllen = 23
            imglen = 24
        self.maxlength = 280 - (urllen + 1) - imglen

        # Start-up banner
        print(f"This is PapersBot running at {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        if self.api:
            last = self.api.user_timeline(count=1)[0].created_at
            print(f"Last tweet was posted at {last} (UTC)")
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
        image = findImage(entry)
        image_file = downloadImage(image)
        if image_file:
            print(f"IMAGE: {image}")
            if self.api:
                media = [self.api.media_upload(image_file).media_id]
            os.remove(image_file)

        print(f"TWEET: {tweet_body}\n")
        if self.api:
            self.api.update_status(tweet_body, media_ids=media)

        self.addToPosted(entry.id)
        self.n_tweeted += 1

        if self.api:
            time.sleep(self.wait_time)

    # Main function, iterating over feeds and posting new items
    def run(self):
        for feed in self.feeds:
            parsed_feed = feedparser.parse(feed)
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
        tweets = self.api.user_timeline(count=200)
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
