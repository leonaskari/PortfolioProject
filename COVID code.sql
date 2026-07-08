--An overview of Covid in Iran
SELECT * 
FROM irancovid;

-- A look at the Total Deaths VS Total Cases in Iran
--Shows the likelihood of dying if you contract COVID

SELECT location, 
date, 
total_cases, 
total_deaths,
ROUND(CAST(total_deaths AS FLOAT) / CAST(total_cases AS FLOAT)*100, 2) AS DeathRatePercentage
FROM irancovid;

--An overview of Covid in UK
SELECT *
FROM ukcovid;

--A look at the Total Deaths Vs Total Cases in UK
--Shows the likelihood of dying if you contract COVID
SELECT location, 
date, 
total_cases, 
total_deaths,
ROUND(CAST(total_deaths AS FLOAT) / CAST(total_cases AS FLOAT)*100, 2) AS DeathRatePercentage
FROM ukcovid;

--A look at the Total Cases vs Population in Iran
--Shows what population of Iran has gotten COVID over time
SELECT location, 
date, 
total_cases, 
population,
ROUND(CAST(total_cases AS FLOAT) / CAST(population AS FLOAT)*100, 5) AS Percentage_of_Iran_with_COVID
FROM irancovid;

--A look at the Total Cases vs Population in UK
--Shows what population of UK has gotten COVID over time
SELECT location, 
date,
total_cases, 
population, 
ROUND(CAST(total_cases AS float) / CAST(population AS float)*100, 5) AS Percentage_of_UK_with_COVID
FROM ukcovid;

--A look at Iran COVID rate vs UK COVID rate as a country
--Shows the percentage of each country over time that has contracted corona or infact just the number of cases that were present in each country
   SELECT ic.location,
   uc.location,
   ic.date,
   ROUND(CAST(ic.total_cases AS FLOAT) / CAST(ic.population AS FLOAT)*100, 5) AS Percentage_of_IRAN_with_COVID, 
   ROUND(CAST(uc.total_cases AS FLOAT) / CAST(uc.population AS FLOAT)*100, 5) AS Percentage_of_UK_with_COVID
   FROM irancovid AS ic 
   FULL JOIN ukcovid AS uc
   ON ic.date=uc.date;

--A look at the Death rate IRAN vs UK
SELECT ic.date,
ic.total_cases AS Total_cases_in_Iran, 
ic.total_deaths AS Total_deaths_in_IRAN, 
ROUND(CAST(ic.total_deaths AS FLOAT) / CAST(ic.total_cases AS FLOAT)*100, 5) AS Death_Rate_Percentage_IRAN, 
uc.total_cases AS Total_cases_in_UK,  
uc.total_deaths AS Total_deaths_in_UK ,
ROUND(CAST(uc.total_deaths AS FLOAT) / CAST(uc.total_cases AS FLOAT)*100, 5) AS Death_Rate_Percentage_UK
   FROM irancovid AS ic 
   FULL JOIN ukcovid AS uc
   ON ic.date=uc.date;

--Showing how many people actually died in IRAN vs UK over time

SELECT ic.date, 
ic.total_deaths AS "IRAN deaths",
uc.total_deaths AS "UK deaths"
FROM irancovid AS ic
FULL join ukcovid AS uc
ON ic.date=uc.date;

--The total death count in Iran and UK
SELECT  MAX(CAST(ic.Total_deaths AS INT)) AS TotalDeathCountIRAN,
MAX(CAST(uc.Total_deaths AS INT)) AS TotalDeathCountUK
FROM irancovid AS ic
FULL JOIN ukcovid AS uc
ON ic.location=uc.location
ORDER BY TotalDeathCountIRAN ASC, TotalDeathCountUK ASC;


--What percentage of the population died? IRAN vs UK
SELECT ic.date,
ic.population AS "Iran population", 
ic.total_deaths AS "Iran deaths",
ROUND(CAST(ic.total_deaths AS FLOAT)/ CAST(ic.population AS FLOAT)*100,6) AS "Percentage of Iran that died from COVID",
uc.population AS "UK population", 
uc.total_deaths AS "UK deaths",
ROUND(CAST(uc.total_deaths AS FLOAT)/ CAST(uc.population AS FLOAT)*100,6) AS "Percentage of UK that died from COVID"
FROM irancovid AS ic
FULL JOIN ukcovid AS UC
ON ic.date = uc.date;
  
--An overview of Global Covid Rates
SELECT *
FROM globalcovid
WHERE continent IS NOT NULL
ORDER BY location ASC;

--Showing countries with the highest total number of deaths. We can use this to see where Iran and UK lie on the tables
SELECT TOP 20 location, 
MAX(CAST(total_deaths as INT)) AS Total_Deaths
FROM globalcovid
WHERE continent IS NOT NULL
GROUP BY location
ORDER BY Total_Deaths DESC;

--Showing countries with the highest total number of cases. We can use this to see where Iran and UK lie on the tables
SELECT TOP 20 location,
MAX(CAST(total_cases AS INT)) AS Total_Cases
FROM globalcovid
WHERE continent IS NOT NULL
GROUP BY location
ORDER BY Total_Cases DESC;

-- Comparing GDP per capita to the Total Deaths
--Clearly there is no correlation

SELECT location,
gdp_per_capita, 
MAX(CAST(total_deaths AS INT)) AS Total_Deaths
FROM globalcovid
GROUP BY location, gdp_per_capita
ORDER BY gdp_per_capita DESC;

--Looking at Iran population vs Vaccination

--What total amount of Iran population was vaccinated
SELECT * FROM vaccinations;

SELECT ic.location, 
ic.date, ic.population,
v.people_vaccinated, 
CAST(v.people_vaccinated AS FLOAT)/CAST(ic.population as float)*100 AS "Percentage of IRAN vaccined"
FROM irancovid as ic
FULL JOIN vaccinations as v
ON ic.location=v.location
AND ic.date=v.date
WHERE v.location= 'Iran';

--Looking at UK population vs Vaccination
--What total amount of UK population was vaccinated

SELECT uc.location,
uc.date, 
uc.population,
v.people_vaccinated, 
CAST(v.people_vaccinated AS FLOAT)/CAST(uc.population AS FLOAT)*100 AS "Percentage of UK vaccined"
FROM ukcovid AS uc
FULL JOIN vaccinations AS v
ON uc.location=v.location
AND uc.date=v.date
WHERE v.location= 'United Kingdom';


