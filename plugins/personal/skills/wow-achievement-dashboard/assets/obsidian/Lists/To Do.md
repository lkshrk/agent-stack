---
title: Achievement Todo
tags: [wow, achievements, view]
---

# Achievement Todo

[[{{ROOT}}/Start Here|Start Here]]

[[{{ROOT}}/Lists/Waiting & Events|Check what is up — rotations and resets]]

[[{{ROOT}}/Dashboard|Dashboard]] · [[{{ROOT}}/Lists/To Do|To Do]] · [[{{ROOT}}/Lists/Future Goals|Future Goals]] · [[{{ROOT}}/Lists/Completed|Completed]] · [[{{ROOT}}/Lists/Shopping|Shopping]]

Check a task to report it complete. It leaves this view and appears under manually reported completion. Your original instructions and annotations stay in the source record.

```dataviewjs
await dv.view("{{ROOT}}/_System/Data/achievement-view", {
  mode: "todo",
  dataPath: "{{ROOT}}/_System/Data/achievement-data.json",
  modelPath: "{{ROOT}}/_System/Data/achievement-model.cjs"
});
```

> [!info]- Supporting records and fallback
> If the view is unavailable, use [[{{ROOT}}/_System/Task Records/Execution|Execution records]], [[{{ROOT}}/_System/Task Records/Research|Research records]] and [[{{ROOT}}/_System/Task Records/Imported completions|Imported completion records]]. Each task exists once; views only display and edit that original.
