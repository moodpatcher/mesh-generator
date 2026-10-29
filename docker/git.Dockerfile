# git without installing git on the host (see git.ps1). Only git + the OpenSSH client.
FROM alpine:3.20
RUN apk add --no-cache git openssh-client
COPY git-entrypoint.sh /usr/local/bin/git-entrypoint.sh
RUN chmod +x /usr/local/bin/git-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/git-entrypoint.sh"]
