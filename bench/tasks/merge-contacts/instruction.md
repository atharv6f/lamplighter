`a.csv` and `b.csv` are two contact lists, each with the header
`name,email,phone`. Merge them into `merged.csv`, with the same header and
one row per email address. Two addresses are the same when they match
without regard to case. The rules:

- Write each address in lowercase.
- Take the name from `a.csv` when the address is in it, and from `b.csv`
  when it is not.
- Take the phone from `a.csv`, unless it is empty there. Then take it from
  `b.csv`, which may be empty too.
- Sort the rows by address.
