Each line of `app.log` is a time, a level and a message, such as
`2026-03-04T09:15:02 ERROR upload refused`. Count the lines whose level is
`ERROR`, per hour, and write `errors.txt` with one line per hour that has
at least one: the hour as `2026-03-04T09`, a space, and the count. Put the
hours in order, earliest first.
