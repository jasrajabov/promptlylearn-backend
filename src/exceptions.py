class NotEnoughCreditsException(Exception):
    """Exception raised when a user does not have enough credits."""

    def __init__(self, message="Not enough credits to perform this action."):
        self.message = message
        super().__init__(self.message)
