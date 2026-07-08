--An insight into all the data that we have present infront of us. Olympics all the way up to Rio 2016.

SELECT *
FROM olympics




--The women with the highest amount of Gold medals up to 2016

SELECT TOP 10 name, sex, team, COUNT(medal) as NumberOfMedals
FROM olympics
WHERE sex = 'F' and medal = 'Gold'
GROUP BY name, sex, team
ORDER BY COUNT(medal) DESC;

--The men with the highest amount of Gold medals up to 2016
SELECT TOP 10 name, sex, team, COUNT(medal) as NumberOfGoldMedals
FROM olympics
WHERE sex = 'M' and medal = 'Gold'
GROUP BY name, sex, team
ORDER BY COUNT(medal) DESC;

--woman with the most overall medals

SELECT TOP 20 name, sex, team, COUNT(medal) as TotalNumberOfMedals
FROM olympics
WHERE sex = 'F' AND medal <> 'NA'
GROUP BY name, sex, team
ORDER BY COUNT(medal) DESC;

--man with the most overall medals
SELECT TOP 20 name, sex, team, COUNT(medal) as TotalNumberOfMedals
FROM olympics
WHERE sex = 'M' AND medal <> 'NA'
GROUP BY name, sex, team
ORDER BY COUNT(medal) DESC;


--top 5 oldest athlete and youngest athletes to have participated
SELECT TOP 5 name, sex, age, team, event, year
FROM olympics
WHERE age IS NOT NULL AND event NOT LIKE 'Art%'
GROUP BY name, age, sex, team, event, year
ORDER by age DESC

SELECT TOP 5 name, sex, age, team, event, year
FROM olympics
WHERE age IS NOT NULL 
GROUP BY name, age, sex, team, event, year
ORDER by age ASC


--Team with the most medals overall world wide
SELECT top 20 team, COUNT(medal) as 'Total Number of Medals'
FROM olympics
WHERE medal <> 'NA'
GROUP BY team
ORDER BY COUNT(medal) DESC

--Team with the most gold medals

SELECT top 20 team, COUNT(medal) as 'Total Number of Medals'
FROM olympics
WHERE medal = 'Gold'
GROUP BY team
ORDER BY COUNT(medal) DESC
