class BaseDrive:
    """
    Optional shared interface for student drive modules.
    """

    def forward(self, power=50):
        raise NotImplementedError

    def backward(self, power=50):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def drive(self, throttle, steering=0):
        raise NotImplementedError

    def status(self):
        return {}
