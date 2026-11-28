#!/bin/bash
set -eu
cd /app
sed -i 's/sum(price for price, quantity in lines)/sum(price * quantity for price, quantity in lines)/' orders.py
