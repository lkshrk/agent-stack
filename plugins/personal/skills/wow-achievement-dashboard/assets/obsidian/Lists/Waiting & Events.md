---
title: Waiting & Events
tags: [wow, achievements, availability]
---

# Waiting & Events

[[{{ROOT}}/Start Here|Start Here]]

[[{{ROOT}}/Dashboard|Dashboard]] · [[{{ROOT}}/Lists/To Do|Normal todo]]

Rotating world quests, stories, events and recurring reset-dependent work are kept here. Rows follow canonical completion status and are sorted by expansion and area. Each command has its own copy button. Commands are displayed, never executed by Obsidian.

```dataviewjs
await dv.view("{{ROOT}}/_System/Data/achievement-view", {mode: "availability", dataPath: "{{ROOT}}/_System/Data/achievement-data.json", modelPath: "{{ROOT}}/_System/Data/achievement-model.cjs"});
```

An exact quest-status query is different from a map or NPC inspection. A quest not shown to this character may be completed, locked, phased, or not loaded; it is not proof that the event is globally down. Quest-completion flags do not establish present availability. Check commands require current client/API validation and should be marked untested until run in game.
