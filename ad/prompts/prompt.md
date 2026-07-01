# General instructions

This repo is used for agent orchestrator tool / framework. 
Agent orchestrator frameowrk is supposed to - 
1. Orchestrate multiple agent based workflows to drive the tasks to completion. 
2. Define/ orchestrate the dependancy graphs through DAG or some tools and have ability to define requirements, inputs / outputs through artifacts / file paths, cron job style periodic tasks etc. 
3. Orchestrator should run through structured files like json/yaml etc. and dependencies input, output , all metadata should be defined in structured files only. Actual file contents can be md files or source codes or directories or anything for that matter. 
4. Always keep the main thread contet clean as much possible. Spawn subagents for multiple works. 
5. Compact every few iterations - when the context is bloating so that token cost is limited. 


---

# Current Ask is this  - 

## E2E Tests addition
**Add new e2e tests covering full paths from absolute boundaries from user perspective to core engine** 
e.g. invoke the ao tool and check if workflows working as expected, check if loggings are generated as expected, check if epics / tasks / outputs are created as expected. 
Create following types of tests 
1. Fixture based tests
2. Reproducible tests - same set of inputs each time, the expectations are also deterministic. as AO runs on LLM, we may not check exacrt contents as those may change each time even for same set of inputs - so in deterministic tests - we should just test the file neames / paths are exactly as expected etc - which will always be reproducible. 
3. Fuzzy / undeterministic tests  - these may test for different set of random inputs OR even of same set of ibputs - where the output may change each time. 
4. performance and correctnes tests. 


Test Areas: 
1. Workflow and DAG, dependencies
2. Token computation / assumptions 
3. Dynamic inputs / dependency specification
4. Logging
5. Agent output being captured for individual steps / tasks. 
6. CLI and flags being exercised correctly e2e. 

### Potentially track the reproducible problem statements. 
1. Maybe create a directory like playground or something in current repo. 
2. Use thta directory to run different examples / workflows using ao. Some directories within playground can be like - sum-of-array, student-data, sorting etc. 
3. these examples should be simple problem statements - that will test the workflows e2e - but should not consume too many tokens to test. Yes there would be burn - but we will tailor the problems to have the low burn rate. 
4. For each of these workflows - we should define workflows like - 
    1. architect - design (consider that tasks should be able to breakdown) - HLD + LLD + ADR
    2. architect - breakdown the tasks based on design (no design here) 
    3. Reviewer - design review, any missed concerns, ambiguity, tasks and interfaces and test fixures, contracts defined clearly and meaningful. 
    4. For each task -
        1. developer - work on and cmoplete tasks. Dont write unit tests or anything - only busines loggic and relevant integration. 
        2. Test Write - Write the unit / integration tests. Consider design time test fixtures. Write tests by considering user flows and expected outputs - dont bend tests to just pass the business logic. 
        3. Reviewer - review individual tasks - also against design. 
    5. Final reviewer - Once all tasks are complete - review final work - also against design check if any misses. Create new tasks of found bugs/ reviewes etc and take one more round of dev/review. 


We can have some playground examples with some easy problem statements like 
1. Sort the elements in given array 
2. Find the sum of elements in given array 
3. Write functions  methods to work on student data
    1. Given array of students - with name/age/standard/scores - methods should return - students within given age / score range etc. 


These playground problem statements -  can have workflows defined - by ao repo developer - however those workflows will be executed each time - as part of e2e test of ao repo itself and we will check if ao repo / latest updated code is correctly driving those workflows to completion and all new features are working correctly. 



--- 

# Old Ask

## Input 1 
No Code changes here/ 

1.  How do we ensure that orchestration workflows are working as epected? 
2. dynamic rules / workflows , dependencies as well as logging and all are working as expected? 
3. what tetsts / confiedence we have and are we actually writing tests from as outside as possible - as in as close to end user as possible? 
    1. There can be AND there MUST be unit level testing also (for individual modules / packages), however we should also have comprehensive test cases covering the whole appliacation/tool from end user perspective. Is that happening? Which files cover those tests? 


