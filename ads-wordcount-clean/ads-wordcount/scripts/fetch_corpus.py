"""Downloads the texts (books from Project Gutenberg) into corpus/."""

import os
import urllib.request

BOOKS = {
    "alice_in_wonderland": 11,
    "frankenstein": 84,
    "tale_of_two_cities": 98,
    "dracula": 345,
    "pride_and_prejudice": 1342,
    "sherlock_holmes": 1661,
    "moby_dick": 2701,
    "war_and_peace": 2600,
}

os.makedirs("corpus", exist_ok=True)
for name, number in BOOKS.items():
    print("downloading", name)
    url = f"https://www.gutenberg.org/cache/epub/{number}/pg{number}.txt"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request) as response:
        text = response.read().decode("utf-8-sig")
    # only the book, without the Project Gutenberg licence text before and after it
    text = text.split("*** START OF")[1].split("\n", 1)[1]
    text = text.split("*** END OF")[0]
    with open(f"corpus/{name}.txt", "w", encoding="utf-8") as f:
        f.write(text)
