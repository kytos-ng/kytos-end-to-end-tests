

import os


from mininet.nodelib import DockerHost


OFTEST_IMAGE = os.environ.get("OFTEST_IMAGE", "amlight4dev/oftest:latest")


class OFTestController(DockerHost):


    def __init__(self, name, **kwargs):

        super().__init__(
            name,
            image=OFTEST_IMAGE,
            pull="always",
            volume=[f"/tmp/{name}-logs:/var/log"],
            **kwargs,
        )
