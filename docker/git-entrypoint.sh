#!/bin/sh
# The SSH files arrive read-only at /ssh-in (copies of the one key and known_hosts that git.ps1 chose); ssh wants its
# private key to be 0600 and owned by the user, which a Windows bind mount cannot give, so they are copied.
if [ -d /ssh-in ]; then
  mkdir -p /root/.ssh
  cp /ssh-in/* /root/.ssh/
  chmod 700 /root/.ssh
  chmod 600 /root/.ssh/*
fi
exec git -c safe.directory=/repo "$@"
