"""
Quick test script for the X Publisher.
Run: python scripts/test_x_publisher.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from virtuai.publishers.x_publisher import XPublisher


def main():
    print("=" * 50)
    print("  VirtuAI — X Publisher Test")
    print("=" * 50)

    # Step 1: Initialize
    print("\n[1] Initializing X Publisher...")
    try:
        publisher = XPublisher()
        print("    OK — Publisher created")
    except ValueError as e:
        print(f"    FAIL — {e}")
        return

    # Step 2: Verify credentials
    print("\n[2] Verifying credentials...")
    result = publisher.verify_credentials()
    if result["success"]:
        print(f"    OK — Logged in as @{result['username']} ({result['name']})")
    else:
        print(f"    FAIL — {result['error']}")
        return

    # Step 3: Post a test tweet
    print("\n[3] Posting test tweet...")
    tweet_result = publisher.post_tweet(
        "Systems over hustle. AI over manual. Execution over ideas.\n\n"
        "The founders who win aren't working harder — they're building smarter.\n\n"
        "Follow for daily AI business tactics. \n\n"
        "#AI #Entrepreneurship #BuildInPublic"
    )

    if tweet_result.success:
        print(f"    OK — Tweet posted!")
        print(f"    URL: {tweet_result.tweet_url}")
    else:
        print(f"    FAIL — {tweet_result.error}")


if __name__ == "__main__":
    main()
