"""The DM half of a fake cluster_tools.slack.Slack, for the tests of the tools that send
DMs: a test's fake defines post() (and update() where used) and inherits dm(), which is
post() to the member's DM channel (dm_channel(), the member itself unless overridden)
and records each DM's kind and login in self.dms."""


class FakeDM:
    def dm_channel(self, member):
        return member

    def dm(self, member, text, blocks=None, *, kind, login, log=True):
        channel = self.dm_channel(member)
        ts = self.post(channel, text, blocks)
        if not hasattr(self, "dms"):
            self.dms = []
        self.dms.append((kind, login, log))
        return channel, ts
