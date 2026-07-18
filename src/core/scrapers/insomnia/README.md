# Insomnia classifieds plugin

Tracks searches over insomnia.gr classifieds category listings. One row is one logical
search and may share its listing URL with other rows.

In addition to the shared fields, rows accept:

- `title_include`: every term must occur in the advert title.
- `title_exclude`: none of the terms may occur in the advert title.

The item model includes both term sets in `identity_key()`, so deduplication and state
write-back distinguish searches without changing the URL. The client receives the parsed
row directly and returns a `ListingResult`; every qualifying offer has its own title,
price, and direct URL. An empty offer tuple is a successful no-match check.

The custom `min_advert_price` setting filters implausibly cheap/bait adverts; `0` disables
the floor. Private `tls-client` and Beautiful Soup dependencies live in
`requirements.txt`. Copy `config.example.json` to `config/insomnia.json` before running.
