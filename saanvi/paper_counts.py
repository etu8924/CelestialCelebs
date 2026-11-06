'''
Per-exoplanet research attention: how many papers mention each exoplanet.

For every exoplanet in ../physical_properties/exoplanet_views.csv we send ONE query that ORs
together the common spellings of its name and read the total number of matching papers.

Two sources are supported:
  - "ads"   (default) NASA ADS: journals + arXiv, searched in title/abstract/keywords.
            Needs a free API token saved in ~/.ads/dev_key (never commit the token!).
            Limit: 5,000 queries/day, so the full run takes ~10 minutes.
  - "arxiv" arXiv API: preprints only, no token, but max 1 request / 3 s (~1.5 hours).

Results are saved (and resumed) from exoplanet_paper_counts.csv, so the fetch only runs once.

Run from the saanvi/ folder:   python paper_counts.py
or from the notebook:          from paper_counts import fetch_all_counts
'''

import ast
import os
import re
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
VIEWS_CSV = HERE.parent / "physical_properties" / "exoplanet_views.csv"
NASA_CSV = HERE.parent / "physical_properties" / "exoplanets.csv"
OUT_CSV = HERE / "exoplanet_paper_counts.csv"

HEADERS = {"User-Agent": "CelestialCelebs/1.0 (https://github.com/etu8924/CelestialCelebs)"}

ADS_URL = "https://api.adsabs.harvard.edu/v1/search/query"
ADS_TOKEN_FILE = Path("~/.ads/dev_key").expanduser()
ADS_DELAY_SECONDS = 0.3

ARXIV_URL = "https://export.arxiv.org/api/query"
ARXIV_DELAY_SECONDS = 4  # arXiv asks for at most one request every 3 seconds
RATE_LIMIT_WAIT = 60  # after a "429 Too Many Requests", back off for at least a minute

SOLAR_SYSTEM = {"sun", "moon", "mercury", "venus", "earth", "mars", "jupiter", "saturn",
                "uranus", "neptune", "pluto", "ceres", "eris", "haumea", "makemake", "triton",
                "io", "europa", "ganymede", "callisto", "titan", "enceladus"}

# Wikipedia spells names out ("Proxima Centauri b"); NASA abbreviates ("Proxima Cen b").
# Multi-word constellations come first so they're replaced before their one-word cousins.
CONSTELLATIONS = {
    "canum venaticorum": "cvn", "canis majoris": "cma", "canis minoris": "cmi",
    "comae berenices": "com", "coronae australis": "cra", "coronae borealis": "crb",
    "leonis minoris": "lmi", "piscis austrini": "psa", "trianguli australis": "tra",
    "ursae majoris": "uma", "ursae minoris": "umi",
    "andromedae": "and", "antliae": "ant", "apodis": "aps", "aquarii": "aqr", "aquilae": "aql",
    "arae": "ara", "arietis": "ari", "aurigae": "aur", "bootis": "boo", "caeli": "cae",
    "camelopardalis": "cam", "cancri": "cnc", "capricorni": "cap", "carinae": "car",
    "cassiopeiae": "cas", "centauri": "cen", "cephei": "cep", "ceti": "cet",
    "chamaeleontis": "cha", "circini": "cir", "columbae": "col", "corvi": "crv",
    "crateris": "crt", "crucis": "cru", "cygni": "cyg", "delphini": "del", "doradus": "dor",
    "draconis": "dra", "equulei": "equ", "eridani": "eri", "fornacis": "for",
    "geminorum": "gem", "gruis": "gru", "herculis": "her", "horologii": "hor", "hydrae": "hya",
    "hydri": "hyi", "indi": "ind", "lacertae": "lac", "leonis": "leo", "leporis": "lep",
    "librae": "lib", "lupi": "lup", "lyncis": "lyn", "lyrae": "lyr", "mensae": "men",
    "microscopii": "mic", "monocerotis": "mon", "muscae": "mus", "normae": "nor",
    "octantis": "oct", "ophiuchi": "oph", "orionis": "ori", "pavonis": "pav", "pegasi": "peg",
    "persei": "per", "phoenicis": "phe", "pictoris": "pic", "piscium": "psc", "puppis": "pup",
    "pyxidis": "pyx", "reticuli": "ret", "sagittae": "sge", "sagittarii": "sgr",
    "scorpii": "sco", "sculptoris": "scl", "scuti": "sct", "serpentis": "ser",
    "sextantis": "sex", "tauri": "tau", "telescopii": "tel", "trianguli": "tri",
    "tucanae": "tuc", "velorum": "vel", "virginis": "vir", "volantis": "vol",
    "vulpeculae": "vul",
}
GREEK = {
    "alpha": "alf", "beta": "bet", "gamma": "gam", "delta": "del", "epsilon": "eps",
    "zeta": "zet", "theta": "tet", "iota": "iot", "kappa": "kap", "lambda": "lam",
    "omicron": "omi", "sigma": "sig", "upsilon": "ups", "omega": "ome", "gliese": "gj",
}
GREEK_FULL = {short: long for long, short in GREEK.items() if long != "gliese"}
GREEK_FULL.update({g: g for g in ["eta", "mu", "nu", "xi", "pi", "rho", "tau", "phi", "chi", "psi"]})
GREEK_FULL["ksi"] = "xi"
GREEK_SYMBOL = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "omicron": "ο", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ",
    "upsilon": "υ", "phi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
}


# ---------------------------------------------------------------- names & matching

def clean_name(raw):
    '''Wikipedia list name -> plain planet name ("51 Pegasi b (Dimidium)" -> "51 Pegasi b").'''
    name = raw.replace("\xa0", " ")
    name = re.sub(r"\[.*?\]", "", name)          # "[ru]" interlanguage tags
    name = re.sub(r"\s*\((?:\?|[^()]*)\)\s*$", "", name)  # trailing "(?)" or "(Dimidium)"
    name = re.sub(r"[†?]", "", name)
    name = name.split(" / ")[0]                   # "SWEEPS-11 / SWEEPS-04" -> "SWEEPS-11"
    # two planets run together: "Proxima Centauri b Proxima Centauri d" -> "Proxima Centauri b"
    name = re.split(r"(?<=\s[a-z])\s+(?=[A-Z0-9])", name.strip())[0]
    return re.sub(r"\s+", " ", name).strip()


def abbreviate(name):
    '''Spelled-out Greek letters / constellations -> NASA-style abbreviations.'''
    s = name.lower()
    for long, short in CONSTELLATIONS.items():
        s = re.sub(rf"\b{long}\b", short, s)
    for long, short in GREEK.items():
        s = re.sub(rf"\b{long}(?=\d|\b)", short, s)
    return s


def match_key(name):
    '''Normalized key for matching Wikipedia names to NASA archive names.'''
    return re.sub(r"[\s\-'’]+", "", abbreviate(clean_name(name)))


def spelling_variants(name):
    '''Spacing variants papers use:
    "HD 189733 b" -> "HD 189733b", "HD189733b", "HD189733 b"; "K2-3b" -> "K2-3 b".'''
    out = {name}
    out.add(re.sub(r"(\d)\s+([a-z])$", r"\1\2", name))  # drop space before planet letter
    out.add(re.sub(r"(\d)([a-z])$", r"\1 \2", name))    # add space before planet letter
    # planets of one star in a multiple system: "GJ 667 C c" -> "GJ 667 Cc", "GJ 667Cc"
    m = re.match(r"^(.*\d) ?([A-C]) ?([a-z])$", name)
    if m:
        out |= {f"{m.group(1)} {m.group(2)}{m.group(3)}", f"{m.group(1)}{m.group(2)}{m.group(3)}"}
    for n in list(out):
        out.add(re.sub(r"^([A-Z]{2,4}) (\d)", r"\1\2", n))  # catalog prefix: "HD 189733" -> "HD189733"
    return out


def greek_variants(name):
    '''"beta Pic b" -> {"beta Pic b", "β Pic b"}; also handles NASA's "bet"/"eps" abbreviations.'''
    first, _, rest = name.partition(" ")
    m = re.match(r"([A-Za-z]+)(\d*)$", first)
    if not (m and rest):
        return {name}
    word, num = m.group(1).lower(), m.group(2)
    full = GREEK_FULL.get(word, word if word in GREEK_SYMBOL else None)
    if full is None:
        return {name}
    return {name, f"{full}{num} {rest}", f"{GREEK_SYMBOL[full]}{num} {rest}"}


def name_variants(row):
    '''Every spelling we search for: Wikipedia name, NASA name, spacing and Greek variants.'''
    names = set()
    for n in [row["planet_name"], row.get("nasa_name")]:
        if isinstance(n, str) and n:
            for g in greek_variants(n):  # NASA writes "bet Pic b"; papers write "beta"/"β Pic b"
                names |= spelling_variants(g)
    return sorted(n.replace('"', "") for n in names)


def load_planets():
    '''Exoplanets from the team's Wikipedia list, matched to the NASA Exoplanet Archive.'''
    views = pd.read_csv(VIEWS_CSV)
    parsed = views["0"].apply(ast.literal_eval)
    df = pd.DataFrame({
        "wiki_name": parsed.str[0],
        "wiki_url": parsed.str[1],
        "view_count": pd.to_numeric(views["view_count"], errors="coerce"),
    })
    df["planet_name"] = df["wiki_name"].apply(clean_name)

    # drop solar-system objects (the list includes them "for reference")
    is_ref = df["wiki_name"].str.contains("for reference", case=False)
    is_solar = df["planet_name"].str.lower().isin(SOLAR_SYSTEM)
    df = df[~(is_ref | is_solar)]
    # the same planet can appear twice with different spacing ("PDS 70 b" / "PDS 70b")
    df["key"] = df["planet_name"].apply(match_key)
    df = df.drop_duplicates("key").reset_index(drop=True)

    # several planets link to one shared system page, so their views aren't really theirs
    df["shares_wiki_page"] = df["wiki_url"].duplicated(keep=False)

    # ADS matching ignores case, so the planet "Alpha Centauri Ab" also matches the star pair
    # "Alpha Centauri AB". Flag names where the planet letter could be read as a second star.
    df["ambiguous_name"] = df["planet_name"].str.contains(r"(?:A ?b|B ?c)$", regex=True)

    nasa = pd.read_csv(NASA_CSV, low_memory=False)
    nasa = nasa[nasa["default_flag"] == 1].copy()
    nasa["key"] = nasa["pl_name"].apply(match_key)
    nasa = nasa.drop_duplicates("key")
    df = df.merge(nasa.rename(columns={"pl_name": "nasa_name"}), on="key", how="left")
    return df


# ---------------------------------------------------------------- sources

def build_query(row, source="ads"):
    names = name_variants(row)
    if source == "ads":
        # =abs: is an EXACT phrase match on title + abstract + keywords. Plain abs: lets ADS
        # loosely expand hyphenated survey names, so "OGLE-TR-56b" matched ~650 unrelated
        # OGLE papers. Exact matching + our own spelling variants avoids that.
        return "=abs:(" + " OR ".join(f'"{n}"' for n in names) + ") collection:astronomy"
    return " OR ".join(f'all:"{n}"' for n in names)


def ads_token():
    token = os.environ.get("ADS_API_TOKEN") or (
        ADS_TOKEN_FILE.read_text().strip() if ADS_TOKEN_FILE.exists() else "")
    if not token:
        raise RuntimeError("No ADS token: save it in ~/.ads/dev_key (see ui.adsabs.harvard.edu "
                           "-> Account -> Settings -> API Token)")
    return token


def ads_total(query, retries=5):
    '''Total number of ADS papers matching `query` (None if ADS keeps failing).'''
    headers = {**HEADERS, "Authorization": f"Bearer {ads_token()}"}
    for attempt in range(retries):
        try:
            r = requests.get(ADS_URL, params={"q": query, "rows": 0, "fl": "id"},
                             headers=headers, timeout=60)
            if r.ok:
                return int(r.json()["response"]["numFound"])
            if r.status_code == 429:
                raise RuntimeError("ADS daily limit (5,000 queries) reached; rerun tomorrow, "
                                   "it resumes where it stopped")
        except (requests.RequestException, KeyError, ValueError):
            pass
        time.sleep(2 * (attempt + 1))
    return None


def arxiv_total(query, retries=5):
    '''Total number of arXiv papers matching `query` (None if arXiv keeps failing).

    Note: max_results must be >= 1. With max_results=0 arXiv returns an error page
    whose totalResults is "1", which silently looks like a real answer.
    '''
    attempt, blocked_for = 0, 0
    while attempt < retries:
        try:
            r = requests.get(ARXIV_URL, params={"search_query": query, "max_results": 1},
                             headers=HEADERS, timeout=60)
            if r.ok and "arxiv.org/api/errors" not in r.text:
                return int(re.search(r"<opensearch:totalResults[^>]*>(\d+)<", r.text).group(1))
            if r.status_code == 429:
                # rate-limited: wait it out (doesn't use up a retry), backing off up to 10 min
                wait = min(RATE_LIMIT_WAIT * 2 ** min(blocked_for, 4), 600)
                print(f"  arXiv rate limit (429); waiting {wait}s", flush=True)
                time.sleep(wait)
                blocked_for += 1
                continue
        except (requests.RequestException, AttributeError):
            pass
        attempt += 1
        time.sleep(ARXIV_DELAY_SECONDS * (attempt + 1))
    return None


# ---------------------------------------------------------------- main loop

def fetch_all_counts(source="ads", out_csv=OUT_CSV, verbose=True):
    '''Count papers for every planet, resuming from out_csv if it already exists.'''
    total = ads_total if source == "ads" else arxiv_total
    delay = ADS_DELAY_SECONDS if source == "ads" else ARXIV_DELAY_SECONDS

    planets = load_planets()
    planets["query"] = planets.apply(build_query, axis=1, source=source)

    cols = ["planet_name", "nasa_name", "wiki_url", "view_count", "shares_wiki_page",
            "source", "query", "papers"]
    done = pd.read_csv(out_csv) if Path(out_csv).exists() else pd.DataFrame(columns=cols)
    # a planet is done only if it was counted with the same source AND the same query,
    # so improving the spelling variants automatically re-fetches just the changed planets
    current = planets[["planet_name", "query"]]
    done = done[(done["source"] == source) & done["papers"].notna()]
    done = done.merge(current, on=["planet_name", "query"], how="inner")
    todo = planets[~planets["planet_name"].isin(set(done["planet_name"]))]
    rows = done.to_dict("records")

    for i, (_, row) in enumerate(todo.iterrows(), 1):
        rec = {c: row[c] for c in cols if c not in ("source", "papers")}
        rec["source"] = source
        rec["papers"] = total(row["query"])
        rows.append(rec)
        if verbose:
            print(f"[{len(done) + i}/{len(planets)}] {row['planet_name']}: {rec['papers']}",
                  flush=True)
        if i % 25 == 0:  # checkpoint so a crash doesn't lose progress
            pd.DataFrame(rows, columns=cols).to_csv(out_csv, index=False)
        time.sleep(delay)

    result = pd.DataFrame(rows, columns=cols)
    result.to_csv(out_csv, index=False)
    return result


if __name__ == "__main__":
    import sys
    fetch_all_counts(source=sys.argv[1] if len(sys.argv) > 1 else "ads")
