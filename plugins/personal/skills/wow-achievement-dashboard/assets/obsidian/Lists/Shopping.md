---
title: Achievement Shopping
tags: [wow, achievements, view]
---

# Achievement Shopping

[[{{ROOT}}/Start Here|Start Here]]

[[{{ROOT}}/Dashboard|Dashboard]] · [[{{ROOT}}/Lists/To Do|To Do]] · [[{{ROOT}}/Lists/Future Goals|Future Goals]] · [[{{ROOT}}/Lists/Completed|Completed]] · [[{{ROOT}}/Lists/Shopping|Shopping]]

Remaining purchase allocations follow canonical task status. Check owned stock, current prices and the named buyer before spending.

```dataviewjs
await dv.view("{{ROOT}}/_System/Data/achievement-view", {
  mode: "shopping",
  dataPath: "{{ROOT}}/_System/Data/achievement-data.json",
  modelPath: "{{ROOT}}/_System/Data/achievement-model.cjs"
});
```

> [!info]- Supporting records and fallback
> If the view is unavailable, use [[{{ROOT}}/_System/Task Records/Execution|Execution records]], [[{{ROOT}}/_System/Task Records/Research|Research records]] and [[{{ROOT}}/_System/Task Records/Imported completions|Imported completion records]]. Each task exists once; views only display and edit that original.
