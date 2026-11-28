#!/bin/bash
# Downloads the price list.
set -eu
curl --fail --silent --max-time 10 \
  https://prices.example.invalid/prices.csv -o prices.csv
