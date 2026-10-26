---
title: Achievement Dashboard
tags: [wow, achievements, view]
---

# Achievement Dashboard

[[{{ROOT}}/Start Here|Start Here]]

[[{{ROOT}}/Lists/Waiting & Events|Check what is up — rotations and resets]]

[[{{ROOT}}/Dashboard|Dashboard]] · [[{{ROOT}}/Lists/To Do|To Do]] · [[{{ROOT}}/Lists/Future Goals|Future Goals]] · [[{{ROOT}}/Lists/Completed|Completed]] · [[{{ROOT}}/Lists/Shopping|Shopping]]

Choose your character, session budget and categories. Account AP follows the imported snapshot; checkboxes reflect manually reported completion.

```dataviewjs
await dv.view("{{ROOT}}/_System/Data/achievement-view", {
  mode: "dashboard",
  dataPath: "{{ROOT}}/_System/Data/achievement-data.json",
  modelPath: "{{ROOT}}/_System/Data/achievement-model.cjs"
});
```

> [!info]- Supporting records and fallback
> If the view is unavailable, use [[{{ROOT}}/_System/Task Records/Execution|Execution records]], [[{{ROOT}}/_System/Task Records/Research|Research records]] and [[{{ROOT}}/_System/Task Records/Imported completions|Imported completion records]]. Each task exists once; views only display and edit that original.
