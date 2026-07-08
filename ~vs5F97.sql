--Joining several tables together using the join function in order to create a view 
-- i want the the first name and last name of the customers to be under one field instead of two seperate fields. Therefore i will use the CONCAT function

SELECT
ord.order_id,
CONCAT (cus.first_name,' ', cus.last_name)as Full_name,
cus.city,
cus.state,
ord.order_date,
--Sales volume
SUM(ite.quantity) as 'total units',
--revenue
SUM(ite.quantity * ite.list_price) as revenue,
pro.product_name,
cat.category_name,
sto.store_name,
CONCAT(sta.first_name, ' ', sta.last_name) as sales_rep
FROM sales.orders as ord

JOIN sales.customers as cus
ON ord.customer_id = cus.customer_id
JOIN sales.order_items as ite
ON ord.order_id = ite.order_id
JOIN production.products as pro
ON ite.product_id = pro.product_id
JOIN production.categories as cat
ON pro.category_id = cat.category_id
JOIN sales.stores as sto
ON ord.store_id = sto.store_id
JOIN sales.staffs as sta
ON ord.staff_id = sta.staff_id

GROUP BY ord.order_id,
CONCAT (cus.first_name,' ', cus.last_name),
cus.city,
cus.state,
ord.order_date,
pro.product_name,
cat.category_name,
sto.store_name,
CONCAT(sta.first_name, ' ', sta.last_name) 


 