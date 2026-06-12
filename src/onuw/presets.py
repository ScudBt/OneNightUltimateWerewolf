from onuw.roles import Role

# Preset role decks for each player count.
# Rule: len(roles) == player_count + 3.
PRESETS: dict[int, tuple[Role, ...]] = {
    4: (
        Role.WEREWOLF, Role.WEREWOLF, Role.SEER, Role.ROBBER,
        Role.TROUBLEMAKER, Role.VILLAGER, Role.VILLAGER,
    ),
    5: (
        Role.WEREWOLF, Role.WEREWOLF, Role.MINION, Role.SEER,
        Role.ROBBER, Role.TROUBLEMAKER, Role.INSOMNIAC, Role.VILLAGER,
    ),
    6: (
        Role.WEREWOLF, Role.WEREWOLF, Role.MINION, Role.SEER,
        Role.ROBBER, Role.TROUBLEMAKER, Role.DRUNK, Role.INSOMNIAC, Role.VILLAGER,
    ),
    7: (
        Role.WEREWOLF, Role.WEREWOLF, Role.MINION, Role.SEER,
        Role.ROBBER, Role.TROUBLEMAKER, Role.DRUNK, Role.INSOMNIAC,
        Role.VILLAGER, Role.VILLAGER,
    ),
}
