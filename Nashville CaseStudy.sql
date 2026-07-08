--Cleaning Data in SQL project.
--Using a data set, i am going to clean the data in order to make it more appealing and user friendly.

SELECT *
FROM nashville;

--I am now going to Standardize the Date Format.
--Using CONVERT i standardize the date format into a format that i prefer.

SELECT SaleDateConverted, CONVERT(Date,SaleDate)
FROM nashville;

UPDATE nashville
SET SaleDate = CONVERT(Date, SaleDate);

ALTER TABLE nashville
ADD SaleDateConverted Date;

UPDATE nashville
SET SaleDateConverted = CONVERT(Date, SaleDate);

--Next i am going to populate the Property Address Data
--In order to get the NULL Property Address Data; i self join the table and examine the data to see if there are the identical ParcelID with its PropertyAddress. 
--Having identical ParcelID's i can see that the PropertyAddress for both data must be the same. Therefore we i this data in order to populate the NULL values.

SELECT *
FROM nashville
--WHERE PropertyAddress IS NULL.
ORDER BY ParcelID;

SELECT a.ParcelID, 
a.PropertyAddress, 
b.ParcelID,
b.PropertyAddress,
ISNULL(a.PropertyAddress, b.PropertyAddress)
FROM nashville AS a
JOIN nashville AS b
ON a.ParcelID = b.ParcelID
AND a.[UniqueID ] <> b.[UniqueID ]
WHERE a.PropertyAddress IS NULL;

--Using ISNULL to see if a.PropertyAdress is NULL then populate the values with b.PropertyAddress.
UPDATE a 
SET PropertyAddress =ISNULL(a.PropertyAddress, b.PropertyAddress)FROM nashville AS a
JOIN nashville AS b
ON a.ParcelID = b.ParcelID
AND a.[UniqueID ] <> b.[UniqueID ]
WHERE a.PropertyAddress IS NULL;

--Now i am going to break out the Address into individual columns (Address, City, State).
--By using this, the address becomes a lot more usable, rather than having one long address with everything inside it.

SELECT PropertyAddress
FROM nashville;
--Using a substring. 
SELECT
SUBSTRING(PropertyAddress, 1, CHARINDEX(',', PropertyAddress)-1) AS Address,
SUBSTRING(PropertyAddress, CHARINDEX(',', PropertyAddress)+1, LEN(PropertyAddress)) AS Address
FROM nashville;

ALTER TABLE nashville
ADD PropertySplitAddress NVARCHAR(255);

UPDATE nashville
SET PropertySplitAddress = SUBSTRING(PropertyAddress, 1, CHARINDEX(',', PropertyAddress)-1);

ALTER TABLE nashville
ADD PropertySplitCity NVARCHAR(255);

UPDATE nashville
SET PropertySplitCity = SUBSTRING(PropertyAddress, CHARINDEX(',', PropertyAddress)+1, LEN(PropertyAddress));



--Doing this exact same process for OwnerAddress with a different process. I use PARSENAME instead of SUBSTRING. Same outcome, just a variation.
SELECT OwnerAddress
FROM nashville;

SELECT
PARSENAME(REPLACE(OwnerAddress,',','.'),3),
PARSENAME(REPLACE(OwnerAddress,',','.'),2),
PARSENAME(REPLACE(OwnerAddress,',','.'),1)
FROM nashville;

ALTER TABLE nashville
ADD OwnerSplitAddress NVARCHAR(255);

UPDATE nashville
SET OwnerSplitAddress = PARSENAME(REPLACE(OwnerAddress,',','.'),3);

ALTER TABLE nashville
ADD OwnerSplitCity NVARCHAR(255);

UPDATE nashville
SET OwnerSplitCity = PARSENAME(REPLACE(OwnerAddress,',','.'),2);

ALTER TABLE nashville
ADD OwnerSplitState NVARCHAR(255);

UPDATE nashville
SET OwnerSplitState = PARSENAME(REPLACE(OwnerAddress,',','.'),1);




--Now i am going to change Y and N to Yes and No in 'Sold as Vacant' field. 
--I do this in order to make the data looking symmetrical in this field.

SELECT DISTINCT(SoldAsVacant), COUNT(SoldAsVacant)
FROM nashville
GROUP BY SoldAsVacant
ORDER BY 2

--Do this through a case statement.

SELECT SoldAsVacant, 
CASE WHEN SoldAsVacant = 'Y' THEN 'Yes'
WHEN SoldAsVacant ='N' THEN 'No'
ELSE SoldAsVacant
END
FROM nashville;
--I use UPDATE in order to update the table.
UPDATE nashville
SET SoldAsVacant = CASE WHEN SoldAsVacant = 'Y' THEN 'Yes'
WHEN SoldAsVacant ='N' THEN 'No'
ELSE SoldAsVacant
END

--Next i am going to remove all the duplicates from the Data Set (in standard practice i shouldn't be deleting data, but i will in this project).

WITH RowNumCTE As(
SELECT * ,
ROW_NUMBER() OVER (
PARTITION BY ParcelID,
PropertyAddress,
SalePrice,
SaleDate,
LegalReference
ORDER BY UniqueID 
) row_num
FROM nashville
--ORDER BY ParcelID.
)
-- We then 
--DELETE 
--FROM RowNumCTE
--WHERE row_num > 1

SELECT *
FROM RowNumCTE
WHERE row_num > 1
ORDER BY PropertyAddress;


--Now i am going to Delete the unused columns (not standard practice).



ALTER TABLE nashville
DROP COLUMN OwnerAddress,
TaxDistrict,
PropertyAddress,
SaleDate;

ALTER TABLE nashville
DROP COLUMN 
SaleDate;
