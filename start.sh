#!/bin/sh
set -e

mkdir -p /data/workspace/docs/inbox
mkdir -p /data/workspace/docs/library
mkdir -p /data/workspace/docs/processed
mkdir -p /data/workspace/docs/failed


openclaw gateway
