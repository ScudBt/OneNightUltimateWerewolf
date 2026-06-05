"""Tests for the game engine: deal, night actions, voting, win conditions."""
from __future__ import annotations

import random

import pytest

from onuw.agents import ScriptedAgent
from onuw.engine import (
    apply_night_action,
    build_private_view,
    deal,
    evaluate_win,
    resolve_vote,
    run_night,
)
from onuw.observations import (
    InsomniacWoke,
    LoneWolfPeek,
    Robbed,
    SawWerewolves,
    SeerPeekedCenter,
    SeerPeekedPlayer,
    TroublemakerSwapped,
)
from onuw.roles import Role
from onuw.state import (
    GameConfig,
    GameState,
    LoneWolfPeekAction,
    NoAction,
    Outcome,
    RobberStealAction,
    SeerPeekCenterAction,
    SeerPeekPlayerAction,
    TroublemakerSwapAction,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_ROLES = (
    Role.WEREWOLF,
    Role.WEREWOLF,
    Role.SEER,
    Role.ROBBER,
    Role.TROUBLEMAKER,
    Role.INSOMNIAC,
    Role.VILLAGER,
    Role.VILLAGER,
)  # 5 players + 3 center = 8 cards


def make_config(roles: tuple[Role, ...] = DEFAULT_ROLES, seed: int = 42) -> GameConfig:
    return GameConfig(player_count=len(roles) - 3, roles=roles, seed=seed)


def make_state_with_dealt(dealt: dict[int, Role]) -> GameState:
    """Construct a GameState with a specific deal."""
    player_count = sum(1 for k in dealt if k < len(dealt) - 3)
    player_count = max(k for k in dealt) - 2  # center slots are last 3
    # Actually derive player_count from the number of positions minus 3 centers
    num_positions = len(dealt)
    player_count = num_positions - 3
    return GameState(
        player_count=player_count,
        dealt_roles=dict(dealt),
        current_roles=dict(dealt),
        public_log=[],
    )


def make_state(player_count: int, role_list: list[Role]) -> GameState:
    """Directly assign roles to positions 0..player_count+2."""
    assert len(role_list) == player_count + 3
    dealt = {i: role_list[i] for i in range(player_count + 3)}
    return GameState(
        player_count=player_count,
        dealt_roles=dict(dealt),
        current_roles=dict(dealt),
        public_log=[],
    )


# ---------------------------------------------------------------------------
# Deal tests
# ---------------------------------------------------------------------------

class TestDeal:
    def test_exact_card_count(self) -> None:
        config = make_config()
        rng = random.Random(1)
        state = deal(config, rng)
        assert len(state.dealt_roles) == config.player_count + 3

    def test_each_role_used_exactly_once(self) -> None:
        config = make_config()
        rng = random.Random(1)
        state = deal(config, rng)
        from collections import Counter
        assert Counter(state.dealt_roles.values()) == Counter(config.roles)

    def test_player_seats_are_first(self) -> None:
        config = make_config()
        rng = random.Random(1)
        state = deal(config, rng)
        assert set(state.player_positions()) == set(range(config.player_count))
        assert set(state.center_positions()) == set(
            range(config.player_count, config.player_count + 3)
        )

    def test_dealt_equals_current_initially(self) -> None:
        config = make_config()
        rng = random.Random(1)
        state = deal(config, rng)
        assert state.dealt_roles == state.current_roles

    def test_invalid_role_count_raises(self) -> None:
        with pytest.raises(ValueError):
            GameConfig(player_count=5, roles=(Role.WEREWOLF,) * 7, seed=0)


# ---------------------------------------------------------------------------
# Werewolf night action
# ---------------------------------------------------------------------------

class TestWerewolfNight:
    def test_two_wolves_see_each_other(self) -> None:
        # Seats 0, 1 are wolves; seats 2-4 are non-wolves
        state = make_state(5, [
            Role.WEREWOLF, Role.WEREWOLF, Role.SEER,
            Role.VILLAGER, Role.VILLAGER,
            Role.ROBBER, Role.TROUBLEMAKER, Role.INSOMNIAC,
        ])
        apply_night_action(state, 0, NoAction())
        apply_night_action(state, 1, NoAction())

        obs0 = state.observations_for(0)
        obs1 = state.observations_for(1)

        assert len(obs0) == 1
        assert isinstance(obs0[0], SawWerewolves)
        assert obs0[0].wolf_positions == (1,)

        assert len(obs1) == 1
        assert isinstance(obs1[0], SawWerewolves)
        assert obs1[0].wolf_positions == (0,)

    def test_lone_wolf_sees_empty_wolves_and_center_peek(self) -> None:
        state = make_state(5, [
            Role.WEREWOLF, Role.SEER, Role.VILLAGER,
            Role.VILLAGER, Role.VILLAGER,
            Role.ROBBER, Role.TROUBLEMAKER, Role.INSOMNIAC,
        ])
        # Center positions are 5, 6, 7; pick center 5
        action = LoneWolfPeekAction(center_position=5)
        apply_night_action(state, 0, action)

        obs = state.observations_for(0)
        assert len(obs) == 2
        assert isinstance(obs[0], SawWerewolves)
        assert obs[0].wolf_positions == ()  # empty — lone wolf
        assert isinstance(obs[1], LoneWolfPeek)
        assert obs[1].center_position == 5
        assert obs[1].role == state.current_roles[5]

    def test_lone_wolf_must_use_peek_action(self) -> None:
        state = make_state(5, [
            Role.WEREWOLF, Role.SEER, Role.VILLAGER,
            Role.VILLAGER, Role.VILLAGER,
            Role.ROBBER, Role.TROUBLEMAKER, Role.INSOMNIAC,
        ])
        with pytest.raises(ValueError):
            apply_night_action(state, 0, NoAction())

    def test_multi_wolf_must_use_no_action(self) -> None:
        state = make_state(5, [
            Role.WEREWOLF, Role.WEREWOLF, Role.SEER,
            Role.VILLAGER, Role.VILLAGER,
            Role.ROBBER, Role.TROUBLEMAKER, Role.INSOMNIAC,
        ])
        with pytest.raises(ValueError):
            apply_night_action(state, 0, LoneWolfPeekAction(5))


# ---------------------------------------------------------------------------
# Seer night action
# ---------------------------------------------------------------------------

class TestSeerNight:
    def _seer_state(self) -> GameState:
        return make_state(5, [
            Role.SEER, Role.WEREWOLF, Role.VILLAGER,
            Role.VILLAGER, Role.VILLAGER,
            Role.ROBBER, Role.TROUBLEMAKER, Role.WEREWOLF,
        ])

    def test_player_peek_returns_current_role(self) -> None:
        state = self._seer_state()
        apply_night_action(state, 0, SeerPeekPlayerAction(target=1))
        obs = state.observations_for(0)
        assert len(obs) == 1
        assert isinstance(obs[0], SeerPeekedPlayer)
        assert obs[0].target == 1
        assert obs[0].role == Role.WEREWOLF

    def test_center_peek_returns_two_roles(self) -> None:
        state = self._seer_state()
        # Center slots: 5=ROBBER, 6=TROUBLEMAKER, 7=WEREWOLF
        apply_night_action(state, 0, SeerPeekCenterAction(targets=(5, 7)))
        obs = state.observations_for(0)
        assert len(obs) == 1
        assert isinstance(obs[0], SeerPeekedCenter)
        assert obs[0].targets == (5, 7)
        assert set(obs[0].roles) == {Role.ROBBER, Role.WEREWOLF}

    def test_seer_cannot_peek_self(self) -> None:
        from onuw.engine import get_legal_actions
        state = self._seer_state()
        legal = get_legal_actions(state, 0)
        targets = [
            a.target for a in legal if isinstance(a, SeerPeekPlayerAction)
        ]
        assert 0 not in targets


# ---------------------------------------------------------------------------
# Robber night action
# ---------------------------------------------------------------------------

class TestRobberNight:
    def _robber_state(self) -> GameState:
        return make_state(5, [
            Role.VILLAGER, Role.ROBBER, Role.WEREWOLF,
            Role.SEER, Role.VILLAGER,
            Role.TROUBLEMAKER, Role.INSOMNIAC, Role.VILLAGER,
        ])

    def test_roles_are_swapped(self) -> None:
        state = self._robber_state()
        # Robber at seat 1 robs seat 2 (WEREWOLF)
        apply_night_action(state, 1, RobberStealAction(target=2))
        assert state.current_roles[1] == Role.WEREWOLF
        assert state.current_roles[2] == Role.ROBBER

    def test_observation_reports_new_role(self) -> None:
        state = self._robber_state()
        apply_night_action(state, 1, RobberStealAction(target=2))
        obs = state.observations_for(1)
        assert len(obs) == 1
        assert isinstance(obs[0], Robbed)
        assert obs[0].target == 2
        assert obs[0].new_role == Role.WEREWOLF  # what robber now holds

    def test_robbed_player_gets_no_observation(self) -> None:
        state = self._robber_state()
        apply_night_action(state, 1, RobberStealAction(target=2))
        assert state.observations_for(2) == []

    def test_dealt_roles_unchanged(self) -> None:
        state = self._robber_state()
        apply_night_action(state, 1, RobberStealAction(target=2))
        assert state.dealt_roles[1] == Role.ROBBER
        assert state.dealt_roles[2] == Role.WEREWOLF


# ---------------------------------------------------------------------------
# Troublemaker night action
# ---------------------------------------------------------------------------

class TestTroublemakerNight:
    def _tm_state(self) -> GameState:
        return make_state(5, [
            Role.SEER, Role.WEREWOLF, Role.TROUBLEMAKER,
            Role.ROBBER, Role.VILLAGER,
            Role.INSOMNIAC, Role.VILLAGER, Role.VILLAGER,
        ])

    def test_two_targets_are_swapped(self) -> None:
        state = self._tm_state()
        # Troublemaker at seat 2 swaps seats 0 and 1
        apply_night_action(state, 2, TroublemakerSwapAction(0, 1))
        assert state.current_roles[0] == Role.WEREWOLF
        assert state.current_roles[1] == Role.SEER

    def test_no_role_info_in_observation(self) -> None:
        state = self._tm_state()
        apply_night_action(state, 2, TroublemakerSwapAction(0, 1))
        obs = state.observations_for(2)
        assert len(obs) == 1
        assert isinstance(obs[0], TroublemakerSwapped)
        assert obs[0].target_a == 0
        assert obs[0].target_b == 1

    def test_targets_get_no_observation(self) -> None:
        state = self._tm_state()
        apply_night_action(state, 2, TroublemakerSwapAction(0, 1))
        assert state.observations_for(0) == []
        assert state.observations_for(1) == []


# ---------------------------------------------------------------------------
# Insomniac night action
# ---------------------------------------------------------------------------

class TestInsomniacNight:
    def test_insomniac_sees_current_role_unchanged(self) -> None:
        state = make_state(5, [
            Role.VILLAGER, Role.VILLAGER, Role.INSOMNIAC,
            Role.VILLAGER, Role.VILLAGER,
            Role.WEREWOLF, Role.SEER, Role.ROBBER,
        ])
        apply_night_action(state, 2, NoAction())
        obs = state.observations_for(2)
        assert len(obs) == 1
        assert isinstance(obs[0], InsomniacWoke)
        assert obs[0].final_role == Role.INSOMNIAC

    def test_insomniac_who_was_robbed_sees_new_role(self) -> None:
        # Seat 0 = ROBBER, seat 1 = INSOMNIAC
        state = make_state(5, [
            Role.ROBBER, Role.INSOMNIAC, Role.VILLAGER,
            Role.VILLAGER, Role.VILLAGER,
            Role.WEREWOLF, Role.SEER, Role.TROUBLEMAKER,
        ])
        # Robber acts first
        apply_night_action(state, 0, RobberStealAction(target=1))
        # After robbery: seat 0 = INSOMNIAC, seat 1 = ROBBER
        apply_night_action(state, 1, NoAction())
        obs = state.observations_for(1)
        assert len(obs) == 1
        assert isinstance(obs[0], InsomniacWoke)
        assert obs[0].final_role == Role.ROBBER  # they now hold the Robber card


# ---------------------------------------------------------------------------
# Ordering interaction tests
# ---------------------------------------------------------------------------

class TestOrderingInteractions:
    def test_seer_sees_pre_robbery_role(self) -> None:
        """Seer peeks seat 3 (WEREWOLF), then Robber robs seat 3.
        Seer's observation must reflect WEREWOLF (the role at peek time)."""
        state = make_state(5, [
            Role.SEER, Role.ROBBER, Role.VILLAGER,
            Role.WEREWOLF, Role.VILLAGER,
            Role.INSOMNIAC, Role.TROUBLEMAKER, Role.VILLAGER,
        ])
        # Seer acts first
        apply_night_action(state, 0, SeerPeekPlayerAction(target=3))
        # Then Robber robs seat 3
        apply_night_action(state, 1, RobberStealAction(target=3))

        seer_obs = state.observations_for(0)
        assert isinstance(seer_obs[0], SeerPeekedPlayer)
        assert seer_obs[0].role == Role.WEREWOLF  # pre-robbery

        # Sanity: current roles are swapped
        assert state.current_roles[1] == Role.WEREWOLF
        assert state.current_roles[3] == Role.ROBBER

    def test_insomniac_robbed_sees_post_swap_role(self) -> None:
        """Robber at seat 0 robs Insomniac at seat 4.
        Insomniac wakes last and sees ROBBER (the new card they hold)."""
        state = make_state(5, [
            Role.ROBBER, Role.VILLAGER, Role.VILLAGER,
            Role.VILLAGER, Role.INSOMNIAC,
            Role.SEER, Role.WEREWOLF, Role.TROUBLEMAKER,
        ])
        apply_night_action(state, 0, RobberStealAction(target=4))
        apply_night_action(state, 4, NoAction())

        insomniac_obs = state.observations_for(4)
        assert isinstance(insomniac_obs[0], InsomniacWoke)
        assert insomniac_obs[0].final_role == Role.ROBBER


# ---------------------------------------------------------------------------
# Vote resolution
# ---------------------------------------------------------------------------

class TestVoteResolution:
    def test_all_tied_at_one_no_death(self) -> None:
        # 5-player game; each player gets exactly 1 vote
        votes = {0: 1, 1: 2, 2: 3, 3: 4, 4: 0}
        assert resolve_vote(votes) == frozenset()

    def test_clear_plurality_dies(self) -> None:
        votes = {0: 2, 1: 2, 2: 3, 3: 4, 4: 0}
        assert resolve_vote(votes) == frozenset({2})

    def test_two_way_tie_at_max_both_die(self) -> None:
        votes = {0: 1, 1: 2, 2: 1, 3: 2, 4: 3}
        assert resolve_vote(votes) == frozenset({1, 2})

    def test_three_votes_majority(self) -> None:
        votes = {0: 1, 1: 1, 2: 1, 3: 2, 4: 3}
        assert resolve_vote(votes) == frozenset({1})

    def test_empty_votes(self) -> None:
        assert resolve_vote({}) == frozenset()


# ---------------------------------------------------------------------------
# Win condition tests
# ---------------------------------------------------------------------------

class TestWinConditions:
    def _state_with_current(self, player_count: int, current: dict[int, Role]) -> GameState:
        dealt = dict(current)  # dealt == current for these tests (we don't care)
        return GameState(
            player_count=player_count,
            dealt_roles=dealt,
            current_roles=current,
            public_log=[],
        )

    def test_wolf_dies_village_wins(self) -> None:
        state = self._state_with_current(4, {
            0: Role.WEREWOLF, 1: Role.SEER, 2: Role.VILLAGER, 3: Role.VILLAGER,
            4: Role.ROBBER, 5: Role.TROUBLEMAKER, 6: Role.INSOMNIAC,
        })
        result = evaluate_win(state, frozenset({0}))
        assert result.outcome == Outcome.VILLAGE_WIN

    def test_no_wolf_dies_werewolf_wins(self) -> None:
        state = self._state_with_current(4, {
            0: Role.WEREWOLF, 1: Role.SEER, 2: Role.VILLAGER, 3: Role.VILLAGER,
            4: Role.ROBBER, 5: Role.TROUBLEMAKER, 6: Role.INSOMNIAC,
        })
        result = evaluate_win(state, frozenset({1}))  # Seer dies, not wolf
        assert result.outcome == Outcome.WEREWOLF_WIN

    def test_no_wolves_present_no_death_village_wins(self) -> None:
        # All wolves in center
        state = self._state_with_current(4, {
            0: Role.SEER, 1: Role.ROBBER, 2: Role.VILLAGER, 3: Role.INSOMNIAC,
            4: Role.WEREWOLF, 5: Role.WEREWOLF, 6: Role.TROUBLEMAKER,
        })
        result = evaluate_win(state, frozenset())
        assert result.outcome == Outcome.VILLAGE_WIN

    def test_no_wolves_present_someone_dies_no_winner(self) -> None:
        state = self._state_with_current(4, {
            0: Role.SEER, 1: Role.ROBBER, 2: Role.VILLAGER, 3: Role.INSOMNIAC,
            4: Role.WEREWOLF, 5: Role.WEREWOLF, 6: Role.TROUBLEMAKER,
        })
        result = evaluate_win(state, frozenset({2}))
        assert result.outcome == Outcome.NO_WINNER

    def test_robber_who_stole_wolf_is_on_wolf_team(self) -> None:
        """Robber robs Werewolf → ends night holding WEREWOLF card → wolf team.
        Original wolf seat now holds ROBBER → village team."""
        state = make_state(4, [
            Role.ROBBER, Role.WEREWOLF, Role.SEER, Role.VILLAGER,
            Role.TROUBLEMAKER, Role.INSOMNIAC, Role.VILLAGER,
        ])
        # Robber at 0 robs wolf at 1
        apply_night_action(state, 0, RobberStealAction(target=1))
        # End state: seat 0 = WEREWOLF (end-of-night), seat 1 = ROBBER

        # If the (now-ROBBER) seat 1 dies, a Werewolf (seat 0) did NOT die
        result = evaluate_win(state, frozenset({1}))
        assert result.outcome == Outcome.WEREWOLF_WIN

        # If the (now-WEREWOLF) seat 0 dies, a Werewolf died
        result2 = evaluate_win(state, frozenset({0}))
        assert result2.outcome == Outcome.VILLAGE_WIN
