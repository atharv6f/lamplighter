`install.sh` installs a made-up tool under a prefix folder. Run a second
time, it fails, and it would add its line to `etc/profile` again. Change it
so that it can be run any number of times: each run ends with exit code 0
and leaves the same result as one run, with the line in `etc/profile`
once. It must still honour `PREFIX` when it is set.
