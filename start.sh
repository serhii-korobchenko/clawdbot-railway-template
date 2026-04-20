#!/usr/bin/env bash
set -e

node upload-server.js &
openclaw gateway
