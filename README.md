# PapersBot

PapersBot is an academic Twitter bot: it reads RSS feeds from journals and preprint archives, selects papers based on keyword matching, and tweets them.

I ([@fxcoudert](https://twitter.com/fxcoudert)) wrote PapersBot to operate my [@MOF_papers](https://twitter.com/MOF_papers) twitter bot, which tweets papers about metal–organic frameworks and related nanoporous materials.

PapersBot was inspired by (and borrows some code from) [feedr](https://github.com/housed/feedr).

---

## Requirements

Python 3 and the following modules: [beautifulsoup4](https://pypi.org/project/beautifulsoup4/), [feedparser](https://github.com/kurtmckee/feedparser), [tweepy](https://github.com/tweepy/tweepy).

If you have Python 3 installed, you can install those modules with  `pip install bs4 feedparser tweepy`.

## Setup

In order to run PapersBot, you need to do the following:
- Create a file `credentials.yml` which will contain your Twitter app credentials, with four lines:
```
CONSUMER_KEY: "x1F3s..."
CONSUMER_SECRET: "3VNg..."
ACCESS_KEY: "7109..."
ACCESS_SECRET: "AdnA..."
```
If you do not know how to get your Twitter credentials, follow [steps #1 and #2 in this tutorial](https://www.digitalocean.com/community/tutorials/how-to-create-a-twitter-app) to register your app with Twitter and get credentials.
- Adjust the file `feeds.txt` which contains the list of RSS feeds you want to crawl. Lines starting with `#` are ignored.
- Inside the code, adjust the [regular expression](https://en.wikipedia.org/wiki/Regular_expression) that selects the papers of interest
- Some extra parameters can be tweaked in configuration file `config.yml`.

## How to run

PapersBot tracks in a file named `posted.dat` (which it will create) the papers that have already been tweeted. The first time you run it, if there is no `posted.dat` from a prior run, PapersBot can thus post **a lot** of papers. If you want to avoid this, especially on the first run or if it hasn't been run for a long time, use the `--do-not-tweet` option.

`papersbot.py --do-not-tweet` will list the papers it _would_ tweet, without actually tweeting. But papers will still be recorded as tweeted in the `posted.dat` file.

## Other features

- Running `papersbot.py --top-tweets` will give you a list of the 5 top tweets, from the bot's 200 latest tweets. It sorts tweets by adding number of retweets and likes.
