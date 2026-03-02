#!/usr/bin/env python3
"""
Scraper for the 3 WORKING Non-Clarity Sites
Excludes San Francisco (JavaScript compatibility issue)
"""

import json
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from multi_platform_scraper import (
    LiveVoterTurnoutScraper,
    SantaCruzScraper,
    validate_and_secure_filepath
)

# Only the 3 working non-Clarity sites
WORKING_SITES = {
    'San_Mateo': {
        'url': 'https://www.livevoterturnout.com/ENR/sanmateocaenr/18/en/gWJEq_Index_18.html',
        'platform': 'livevoterturnout',
        'scraper_class': LiveVoterTurnoutScraper
    },
    'San_Joaquin': {
        'url': 'https://www.livevoterturnout.com/ENR/sanjoaquincaenr/19/en/Index_19.html',
        'platform': 'livevoterturnout',
        'scraper_class': LiveVoterTurnoutScraper
    },
    'Santa_Cruz': {
        'url': 'https://www2.santacruzcountyca.gov/ElectionSites/ElectionResults/Results',
        'platform': 'santacruz',
        'scraper_class': SantaCruzScraper
    }
}

def scrape_county(county_name, county_info):
    """Scrape a single county"""
    print(f"\n{'='*70}")
    print(f"Scraping: {county_name} ({county_info['platform']})")
    print(f"{'='*70}")
    
    start_time = time.time()
    
    try:
        scraper = county_info['scraper_class'](
            county_info['url'],
            county_name
        )
        
        result = scraper.scrape()
        duration = time.time() - start_time
        
        if result:
            result['county_name'] = county_name
            result['platform'] = county_info['platform']
            result['scrape_duration'] = duration
            
            # Save result
            data_dir = Path("data")
            data_dir.mkdir(exist_ok=True)
            
            # Security: Use secure filepath with validation
            output_file = validate_and_secure_filepath(data_dir, f"{county_name}_working", "json")
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            # Print summary
            vt = result.get('voter_turnout', {})
            contests = result.get('contests', [])
            
            print(f"\n✅ SUCCESS in {duration:.1f}s")
            print(f"\n  Voter Turnout:")
            print(f"    Ballots Cast: {vt.get('ballots_cast', 0):,}")
            print(f"    Registered: {vt.get('registered_voters', 0):,}")
            print(f"    Turnout: {vt.get('turnout_percentage', 0)}%")
            print(f"\n  Contests: {len(contests)}")
            for i, contest in enumerate(contests[:3], 1):
                title = contest.get('title', 'No title')
                choices = contest.get('choices', [])
                print(f"    {i}. {title[:60]} ({len(choices)} choices)")
            print(f"\n  Last Updated: {result.get('last_updated', 'N/A')}")
            print(f"  Saved to: {output_file.name}")
            
            return {
                'county': county_name,
                'success': True,
                'duration': duration,
                'voter_turnout': vt,
                'contest_count': len(contests)
            }
        else:
            print(f"\n❌ FAILED")
            return {'county': county_name, 'success': False}
            
    except Exception as e:
        duration = time.time() - start_time
        print(f"\n❌ ERROR: {e}")
        return {'county': county_name, 'success': False, 'error': str(e)}

def main():
    print("="*70)
    print("NON-CLARITY ELECTION SCRAPER")
    print("="*70)
    print("Scraping 3 counties:")
    print("  • San Mateo (LiveVoterTurnout)")
    print("  • San Joaquin (LiveVoterTurnout)")
    print("  • Santa Cruz (Custom)")
    print("="*70)
    
    results = []
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(scrape_county, county_name, county_info): county_name
            for county_name, county_info in WORKING_SITES.items()
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
    
    total_time = time.time() - start_time
    total_minutes = int(total_time // 60)
    total_seconds = int(total_time % 60)
    
    # Final summary
    print("\n" + "="*70)
    print("FINAL SUMMARY - ALL 3 NON-CLARITY COUNTIES")
    print("="*70)
    
    successful = sum(1 for r in results if r['success'])
    
    print(f"Total Counties: 3")
    print(f"✅ Successful: {successful}/3 ({successful/3*100:.0f}%)")
    print(f"📊 With Full Data: {successful}/3")
    print(f"⏱️  Total Time: {total_minutes}m {total_seconds}s")
    
    print(f"\nDetailed Results:")
    for result in results:
        if result['success']:
            vt = result.get('voter_turnout', {})
            duration = result.get('duration', 0)
            duration_str = f"{int(duration)}s" if duration < 60 else f"{int(duration//60)}m {int(duration%60)}s"
            print(f"  ✅ {result['county']:15} | Turnout: {vt.get('turnout_percentage', 0):5.1f}% | Contests: {result.get('contest_count', 0)} | Time: {duration_str}")
        else:
            print(f"  ❌ {result['county']:15} | FAILED")
    
    # Save summary
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    
    # Security: Use secure filepath with validation
    summary_file = validate_and_secure_filepath(data_dir, "working_summary", "json")
    
    summary = {
        'timestamp': datetime.now().isoformat(),
        'total_counties': 3,
        'successful': successful,
        'total_time': total_time,
        'results': results
    }
    
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 Summary: {summary_file.name}")
    print("="*70)

if __name__ == "__main__":
    main()

