from pytrends.request import TrendReq
import requests

import pandas as pd
import time


def pytrends_query(keywords, anchor, timeframe='2020-01-01 2026-09-30', pause=5):
    '''
    Returns a list of daily search interest for each keyword in the list of 'keywords' between 'start_date' and 'end_date'.
    Need to decide an 'anchor' that will be the point of reference for each batch of keywords. 
    The anchor should be a keyword that is expected to have a relatively stable search interest over time, so that it can be used to normalize the search interest of the other keywords in the batch.

    Make sure to use the correct format for the dates (YYYY-MM-DD).
    '''
    pytrends = TrendReq(hl='en-US', tz=300)
    others = [k for k in keywords if k != anchor]
    batches = [others[i:i+4] for i in range(0, len(others), 4)]

    ref = None
    frames = []
    for batch in batches:
        pytrends.build_payload([anchor] + batch, timeframe=timeframe)
        df = pytrends.interest_over_time().drop(columns='isPartial', errors='ignore')

        if ref is None:
            ref = df[anchor]
            frames.append(df)
        else:
            factor = ref.sum() / df[anchor].sum() if df[anchor].sum() else float('nan')
            frames.append(df[batch] * factor)

        time.sleep(pause)

    combined = pd.concat(frames, axis=1)
    return combined / combined.max().max() * 100


def wiki_pageviews(article_titles, start_date="20200101", end_date='20260930'):
    '''
    Returns a list of daily page views for each article title in the list of 'article_titles' between 'start_date' and 'end_date'.

    Make sure to use the correct format for the dates (YYYYMMDD) and article titles (replace spaces with underscores).
    '''
    output = {article_title: [] for article_title in article_titles}

    for article_title in article_titles:
        url = f'https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia.org/all-access/all-agents/{article_title}/daily/{start_date}/{end_date}'
        headers = {'User-Agent': 'WikiDataScraper/1.0 (https://github.com/etu8924/CelestialCelebs)'}
        resp = requests.get(url, headers=headers)
        data = resp.json()
        if resp.status_code == 404:
            # Either wikimedia api can't find it (check spelling!), hasn't updated, or page has 0 views. Probably won't encounter case that API isn't updated, so just leave as 0 views.
            output[article_title] = 0
        output[article_title] = [item['views'] for item in data.get('items', [])]
    return pd.DataFrame(output)


if __name__ == "__main__":
    output = wiki_pageviews(["Albert_Einstein", "Isaac_Newton"], start_date="20260901")
    print(output)
    df = pytrends_query(["Einstein", "Newton", "Galilei", "Kepler", "Copernicus", "Bohr", "Hubble", "Curie", "Turing"], anchor='Kepler')
    print(df)