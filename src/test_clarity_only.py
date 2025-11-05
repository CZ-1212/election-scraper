#!/usr/bin/env python3
"""Quick test of Clarity sites with bug fix"""

from clarity_scraper import ClarityScraper
import json
from pathlib import Path

CLARITY_SITES = {
    'Contra_Costa': 'https://results.enr.clarityelections.com/CA/Contra_Costa/124407/web.345435/#/summary',
    'Marin': 'https://results.enr.clarityelections.com/CA/Marin/124182/web.345435/#/summary',
    'Santa_Clara': 'https://results.enr.clarityelections.com/CA/Santa_Clara/125157/web.345435/#/summary',
    'Sonoma': 'https://results.enr.clarityelections.com/CA/Sonoma/124354/web.345435/#/summary',
}

print("="*70)
print("CLARITY ELECTION SCRAPER")
print("="*70)
print(f"Scraping all 4 Clarity counties:")
print(f"  • Contra Costa")
print(f"  • Marin")
print(f"  • Santa Clara")
print(f"  • Sonoma")
print("="*70)

import time
from datetime import datetime

results = []
overall_start = time.time()

for i, (county, url) in enumerate(CLARITY_SITES.items(), 1):
    print(f"\n[{i}/4] Scraping {county}...")
    print("-"*70)
    
    county_start = time.time()
    
    scraper = ClarityScraper(url, reuse_driver=None, save_files=False)
    result = scraper.scrape()
    
    county_duration = time.time() - county_start
    
    if result:
        # Get data from selenium_data section
        selenium_data = result.get('selenium_data', {})
        vt = selenium_data.get('voter_turnout', {})
        contests = selenium_data.get('contests', [])
        
        print(f"✅ SUCCESS in {county_duration:.1f}s")
        print(f"\n  Voter Turnout:")
        if vt and vt.get('ballots_cast'):
            print(f"    ✅ Ballots: {vt.get('ballots_cast', 0):,}")
            print(f"    ✅ Registered: {vt.get('registered_voters', 0):,}")
            print(f"    ✅ Turnout: {vt.get('turnout_percentage', 0)}%")
            has_turnout = True
        else:
            print(f"    ❌ NO DATA")
            has_turnout = False
        
        print(f"\n  Contests: {len(contests)}")
        if contests:
            for j, contest in enumerate(contests[:3], 1):
                title = contest.get('title', 'No title')
                choices = contest.get('choices', [])
                print(f"    ✅ {j}. {title[:60]} ({len(choices)} choices)")
            has_contests = True
        else:
            print(f"    ❌ NO CONTESTS")
            has_contests = False
        
        # Save
        data_dir = Path("data")
        data_dir.mkdir(exist_ok=True)
        output_file = data_dir / f"{county}_clarity_fixed.json"
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"\n  💾 Saved: {output_file}")
        
        results.append({
            'county': county,
            'success': True,
            'has_turnout': has_turnout,
            'has_contests': has_contests,
            'contest_count': len(contests),
            'duration': county_duration
        })
    else:
        print(f"❌ FAILED after {county_duration:.1f}s")
        results.append({
            'county': county,
            'success': False,
            'duration': county_duration
        })
    
    # Wait between counties (except after last one)
    if i < 4:
        print(f"\n⏱️  Waiting 5 seconds before next county...")
        time.sleep(5)

# Calculate total time
total_duration = time.time() - overall_start
total_minutes = int(total_duration // 60)
total_seconds = int(total_duration % 60)

# Final summary
print("\n" + "="*70)
print("FINAL SUMMARY - ALL 4 CLARITY COUNTIES")
print("="*70)

successful = sum(1 for r in results if r['success'])
with_turnout = sum(1 for r in results if r.get('has_turnout'))
with_contests = sum(1 for r in results if r.get('has_contests'))

print(f"Total Counties: 4")
print(f"✅ Successful: {successful}/4 ({successful/4*100:.0f}%)")
print(f"📊 With Voter Turnout: {with_turnout}/4")
print(f"🗳️  With Contests: {with_contests}/4")
print(f"⏱️  Total Time: {total_minutes}m {total_seconds}s")

print(f"\nDetailed Results:")
for r in results:
    status = "✅" if r['success'] else "❌"
    turnout = "✅" if r.get('has_turnout') else "❌"
    contests = f"✅ ({r.get('contest_count', 0)})" if r.get('has_contests') else "❌"
    duration = r.get('duration', 0)
    duration_str = f"{int(duration)}s" if duration < 60 else f"{int(duration//60)}m {int(duration%60)}s"
    print(f"  {status} {r['county']:15} | Turnout: {turnout} | Contests: {contests} | Time: {duration_str}")

print("="*70)

