from dataclasses import dataclass


@dataclass
class TimeProfile:
    temperature: float         # Neural net sampling heat — higher = faster/sloppier
    panic_threshold: float     # Seconds remaining when panic mode kicks in
    tactics_probability: float # Tactic probability when clock > low_time_threshold
    low_time_threshold: float = 0.0   # Below this many seconds, use tactics_probability_low
    tactics_probability_low: float = 0.0  # Tactic probability when clock is low
    strategic_probability: float = 0.0   # Probability of applying a positional strategic move
    winning_capture_probability: float = 1.0  # Probability of taking a clearly-winning capture
    rescue_probability: float = 1.0          # Probability of saving a currently-hanging piece
    pawn_rescue_probability: float = 0.70    # Probability of pushing a hanging pawn to safety


TIME_PROFILES: dict[str, TimeProfile] = {
    "bullet": TimeProfile(
        temperature=1.6, panic_threshold=8.0,
        tactics_probability=0.6, low_time_threshold=20.0, tactics_probability_low=0.35,
        strategic_probability=0.20,
        winning_capture_probability=0.82,
        rescue_probability=0.78,
        pawn_rescue_probability=0.45,
    ),
    "blitz":  TimeProfile(temperature=1.0, panic_threshold=6.0, tactics_probability=0.88,
                          strategic_probability=0.30,
                          winning_capture_probability=0.92,
                          rescue_probability=0.90,
                          pawn_rescue_probability=0.62),
    "rapid":  TimeProfile(temperature=0.5, panic_threshold=5.0, tactics_probability=0.95,
                          strategic_probability=0.40,
                          winning_capture_probability=0.97,
                          rescue_probability=0.96,
                          pawn_rescue_probability=0.78),
}


class TimeManager:
    def __init__(self, time_control: str):
        if time_control not in TIME_PROFILES:
            raise ValueError(f"time_control must be one of {list(TIME_PROFILES.keys())}")
        self.time_control = time_control
        self.profile = TIME_PROFILES[time_control]
