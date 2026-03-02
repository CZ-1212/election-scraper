#!/usr/bin/env python3
"""
Run all 7 county scrapers in parallel (4 Clarity + 3 non-Clarity).
"""

import json
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from clarity_scraper import ClarityScraper, validate_and_secure_filepath
from multi_platform_scraper import LiveVoterTurnoutScraper, SantaCruzScraper

# All counties
CLARITY_SITES = {
    'Contra_Costa': 'https://results.enr.clarityelections.com/CA/Contra_Costa/124407/web.345435/#/summary',
    'Marin':        'https://results.enr.clarityelections.com/CA/Marin/124182/web.345435/#/summary',
    'Santa_Clara':  'https://results.enr.clarityelections.com/CA/Santa_Clara/125157/web.345435/#/summary',
    'Sonoma':       'https://results.enr.clarityelections.com/CA/Sonoma/124354/web.345435/#/summary',
}

NON_CLARITY_SITES = {
    'San_Mateo':   {'url': 'https://www.livevoterturnout.com/ENR/sanmateocaenr/18/en/gWJEq_Index_18.html',  'scraper_class': LiveVoterTurnoutScraper},
    'San_Joaquin': {'url': 'https://www.livevoterturnout.com/ENR/sanjoaquincaenr/19/en/Index_19.html',       'scraper_class': LiveVoterTurnoutScraper},
    'Santa_Cruz':  {'url': 'https://www2.santacruzcountyca.gov/ElectionSites/ElectionResults/Results',       'scraper_class': SantaCruzScraper},
}


def scrape_clarity(county, url):
    start = time.time()
    try:
        scraper = ClarityScraper(url, reuse_driver=None, save_files=False)
        result = scraper.scrape()
        duration = time.time() - start
        return county, 'clarity', result, duration, None
    except Exception as e:
        return county, 'clarity', None, time.time() - start, str(e)


def scrape_non_clarity(county, info):
    start = time.time()
    try:
        scraper = info['scraper_class'](info['url'], county)
        result = scraper.scrape()
        duration = time.time() - start
        return county, 'non-clarity', result, duration, None
    except Exception as e:
        return county, 'non-clarity', None, time.time() - start, str(e)


def main():
    print("=" * 70)
    print("CALIFORNIA ELECTION SCRAPER — ALL 7 COUNTIES")
    print("=" * 70)
    print("Clarity (4):     Contra Costa, Marin, Santa Clara, Sonoma")
    print("Non-Clarity (3): San Mateo, San Joaquin, Santa Cruz")
    print("=" * 70)

    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    overall_start = time.time()
    results = []
    county_data = {}  # Accumulates all county results for the combined file

    # Submit all 7 counties concurrently (2 Clarity workers + 3 non-Clarity workers)
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for county, url in CLARITY_SITES.items():
            futures[executor.submit(scrape_clarity, county, url)] = county
        for county, info in NON_CLARITY_SITES.items():
            futures[executor.submit(scrape_non_clarity, county, info)] = county

        for future in as_completed(futures):
            county, platform, result, duration, error = future.result()
            duration_str = f"{int(duration)}s" if duration < 60 else f"{int(duration//60)}m {int(duration%60)}s"

            print(f"\n{'=' * 70}")
            print(f"{'✅' if not error else '❌'} {county} ({platform}) — {duration_str}")
            print(f"{'=' * 70}")

            if error:
                print(f"  ERROR: {error}")
                results.append({'county': county, 'platform': platform, 'success': False, 'duration': duration, 'error': error})
                county_data[county] = {'success': False, 'error': error}
                continue

            if platform == 'clarity':
                selenium_data = result.get('selenium_data', {}) if result else {}
                vt = selenium_data.get('voter_turnout', {})
                contests = selenium_data.get('contests', [])
            else:
                vt = result.get('voter_turnout', {}) if result else {}
                contests = result.get('contests', []) if result else []

            if vt:
                print(f"  Ballots Cast: {vt.get('ballots_cast', 0):,}")
                print(f"  Registered:   {vt.get('registered_voters', 0):,}")
                print(f"  Turnout:      {vt.get('turnout_percentage', 0)}%")
            print(f"  Contests: {len(contests)}")
            for i, c in enumerate(contests[:3], 1):
                print(f"    {i}. {c.get('title', '')[:65]} ({len(c.get('choices', []))} choices)")

            county_data[county] = result

            results.append({
                'county': county,
                'platform': platform,
                'success': True,
                'duration': duration,
                'turnout_pct': vt.get('turnout_percentage'),
                'contest_count': len(contests),
            })

    total_time = time.time() - overall_start
    successful = sum(1 for r in results if r['success'])
    total = len(results)

    print("\n" + "=" * 70)
    print("FINAL SUMMARY — ALL 7 COUNTIES")
    print("=" * 70)
    print(f"✅ Successful: {successful}/{total} ({successful/total*100:.0f}%)")
    print(f"⏱️  Total Time: {int(total_time//60)}m {int(total_time%60)}s")
    print()
    for r in sorted(results, key=lambda x: x['county']):
        status = "✅" if r['success'] else "❌"
        dur = f"{int(r['duration'])}s" if r['duration'] < 60 else f"{int(r['duration']//60)}m {int(r['duration']%60)}s"
        if r['success']:
            print(f"  {status} {r['county']:15} | Turnout: {r.get('turnout_pct', '?'):>5}% | Contests: {r.get('contest_count', 0)} | {dur}")
        else:
            print(f"  {status} {r['county']:15} | FAILED | {dur}")

    # Save all county data into one combined file
    combined_file = validate_and_secure_filepath(data_dir, "all_counties", "json")
    with open(combined_file, 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'total_counties': total,
            'successful': successful,
            'total_time_seconds': total_time,
            'counties': county_data,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n💾 All county data saved to: {combined_file.name}")
    print("=" * 70)


if __name__ == "__main__":
    main()
