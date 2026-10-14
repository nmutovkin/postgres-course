# Module 04: Exercises

<!-- nav:start -->
> [Theory](README.md) · **Exercises** · [Project](project.md) · [Solutions](solutions/README.md)  
> [← 03 Reading and Writing Data](../03-crud/README.md) · [Course home](../../README.md) · [05 Aggregation and Grouping →](../05-aggregation/README.md)
<!-- nav:end -->

**Starting state.** Every exercise runs against the small dataset:

```bash
./project/bin/reset.sh 04
```

Nothing in this module's milestone changes the schema or the data, so the exercises don't depend on it. Exercises 9 and 12 modify data, so wrap your attempts in `BEGIN; ... ROLLBACK;` (or re-run `reset.sh 04` afterwards). "Today" in the dataset is **2025-06-30**: use that fixed date instead of `now()`.

Solutions: [solutions/README.md](solutions/README.md).

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: Customers to win back](#exercise-1--customers-to-win-back)
- [Exercise 2 ★: The unsold catalog](#exercise-2--the-unsold-catalog)
- [Exercise 3 ★: Orders at an old price](#exercise-3--orders-at-an-old-price)
- [Exercise 4 ★★: Shops without a sale](#exercise-4--shops-without-a-sale)
- [Exercise 5 ★★: Empty corners of the catalog](#exercise-5--empty-corners-of-the-catalog)
- [Exercise 6 ★★: Loyal to Europe](#exercise-6--loyal-to-europe)
- [Exercise 7 ★★: Each shop's biggest orders](#exercise-7--each-shops-biggest-orders)
- [Exercise 8 ★★: TechTrove's lapsed customers](#exercise-8--techtroves-lapsed-customers)
- [Exercise 9 ★★: Retiring the T-shirt line](#exercise-9--retiring-the-t-shirt-line)
- [Exercise 10 ★★: Repeat purchases](#exercise-10--repeat-purchases)
- [Exercise 11 ★★★: Cookware-only orders](#exercise-11--cookware-only-orders)
- [Exercise 12 ★★★: Debug the June report](#exercise-12--debug-the-june-report)
- [Exercise 13 ★★★: Prices only ever went up](#exercise-13--prices-only-ever-went-up)

<!-- toc:end -->

## Exercise 1 ★: Customers to win back

Marketing wants to email **active** users who have never placed an order. List their id, name, sign-up date, and how many days had passed between sign-up and 2025-06-30. Longest-waiting first.

**Self-check:** 8 rows. The first is Haruto Sato with 847 days. The last is Samuel Okafor with 5 days. Leo Jensen (28) never ordered either, but must not appear.

<details><summary>Hint</summary>

"Never" is an anti-join. Subtracting one `date` from another gives a number of days.

</details>

---

## Exercise 2 ★: The unsold catalog

List every product that has never been ordered, with its status, its shop name, its category name, and the units available to sell (`quantity - reserved` from `inventory`).

**Self-check:** 9 rows, ids 14, 15, 28, 55, 57, 58, 59, 60, 61. Exactly four of them are active with stock available: 15, 57, 58 and 60. Product 60's category is Garden.

<details><summary>Hint</summary>

The three lookups (seller, category, inventory) are ordinary joins along many-to-one edges, so they can't change the row count. The "never ordered" part is separate from them.

</details>

---

## Exercise 3 ★: Orders at an old price

The finance team wants every order line whose `unit_price` differs from the product's current price: order id, order date, product id and title, the price paid, the current price and the difference (paid minus current). Then, with a second query, count the lines and the distinct orders involved.

**Self-check:** 25 lines in 20 orders. The first line (by order id) is order 2, the AeroBook 13 Laptop, paid 1099.00, now 999.00, difference 100.00. The Dutch Oven appears with two different old prices.

<details><summary>Hint</summary>

`count(DISTINCT col)` counts distinct values. Think about what `<>` would do if either column could be `NULL`. (Neither can, here.)

</details>

---

## Exercise 4 ★★: Shops without a sale

A sale is an order with status `paid`, `shipped` or `delivered`. List the sellers that have never made a sale, with their country and a boolean column `has_products` saying whether they have listed anything at all.

**Self-check:** 2 rows: Maple & Pine (`CA`, `has_products` true) and Sakura Gadgets (`JP`, false).

<details><summary>Hint</summary>

The status filter belongs inside the anti-join's subquery. `EXISTS (...)` is a boolean expression and can be used in the `SELECT` list.

</details>

---

## Exercise 5 ★★: Empty corners of the catalog

Find the categories that have no products **anywhere in their subtree**: not in the category itself, not in its children, not in its grandchildren. Don't use recursion. Rely on the fact that the tree is at most three levels deep.

**Self-check:** 2 rows: 7 Speakers and 20 History. As a contrast, the categories with no products *directly* in them are 7 rows (1, 5, 7, 8, 14, 18, 20), and your query must not return Electronics, Audio, Home & Garden, Books or Non-Fiction.

<details><summary>Hint</summary>

Turn it around: for each product, which categories does it "belong to"? Its own category, the parent of that, and the grandparent. Two self joins on `categories` give you all three. Remember that some products sit in second-level or root categories.

</details>

---

## Exercise 6 ★★: Loyal to Europe

Bazaar has three European shops: Bookworm Corner (GB), Casa Cucina (IT) and Threadbare Co. (DE). Find the customers who have a non-cancelled order from **every** one of them. Identify the shops by country code (`GB`, `IT`, `DE`), not by id, so the query keeps working when a new European shop opens.

**Self-check:** 5 users: 8, 12, 13, 20 and 26. If you get a sixth (Mateo García, 14), look at the status of his Bookworm Corner order.

<details><summary>Hint</summary>

SQL has no "for all". Rephrase: a user qualifies if there is **no** European seller from whom the user has **no** non-cancelled order. That's two nested `NOT EXISTS`.

</details>

---

## Exercise 7 ★★: Each shop's biggest orders

For every seller, show its two largest non-cancelled orders by `total` (order id, total, status). Sellers without such orders must still appear, once, with `NULL`s. Make the result deterministic even if two orders had the same total.

**Self-check:** 10 rows. TechTrove's two are orders 40 (8,940.00) and 114 (4,399.98). Bookworm Corner's second is order 93 (119.48), not the cancelled order 53. Maple & Pine and Sakura Gadgets appear with `∅`.

<details><summary>Hint</summary>

A subquery with `ORDER BY ... LIMIT 2` that refers to the current seller: that's `LATERAL`. Which join keeps sellers for which the subquery returns nothing?

</details>

---

## Exercise 8 ★★: TechTrove's lapsed customers

TechTrove wants to win back customers who had a non-cancelled TechTrove order in 2024 but none in 2025. Use a set operation to find their user ids, then show their names and emails.

**Self-check:** 5 users: 2, 3, 14, 17 and 22. Zoë Martin's email is the mixed-case `Zoe.Martin@Example.COM`.

<details><summary>Hint</summary>

`EXCEPT` gives "in the first set but not in the second". To add names, use the whole set operation as a derived table and join it to `users`.

</details>

---

## Exercise 9 ★★: Retiring the T-shirt line

Threadbare Co. is dropping T-shirts. In one `UPDATE`, archive every **active** product in the category whose slug is `t-shirts`, except products that are still part of an open order (status `pending` or `paid`), since those must still ship. Use `UPDATE ... FROM` to reach the category by its slug, and return the id, title and category name of each archived product. Run it inside a transaction and roll back.

**Self-check:** `UPDATE 2`: products 50 (Linen Summer Shirt) and 51 (Graphic Tee 'Ctrl+Alt+Del'). Products 47, 48 and 49 are protected by open orders.

<details><summary>Hint</summary>

`UPDATE products p SET ... FROM categories c WHERE c.id = p.category_id AND c.slug = ... AND NOT EXISTS (...)`. `RETURNING` can use columns of `c`.

</details>

---

## Exercise 10 ★★: Repeat purchases

Find every pair of non-cancelled orders in which **the same customer** bought **the same product**: user id, product id, the earlier order id and the later order id. Each pair must appear exactly once, and an order must not be paired with itself.

**Self-check:** 29 pairs, covering 27 distinct (user, product) combinations. The first row (sorted by user, product, then the two order ids) is user 8, product 17, orders 19 and 71. Olivia Brown (13) bought product 2 in three orders, which gives three pairs.

<details><summary>Hint</summary>

Join "order line + its order" to a second copy of "order line + its order" on the same product and the same user. One inequality between the two order ids removes both self-pairs and mirrored duplicates.

</details>

---

## Exercise 11 ★★★: Cookware-only orders

Casa Cucina wants to know which orders consisted **only** of Cookware (category 10). List order id, status and total. An order with no lines at all must not count as "only cookware".

**Self-check:** 9 orders: 18, 30, 47, 61, 79, 94, 98, 108, 120. Order 6 has cookware too, but also a bread knife (Cutlery), so it must not appear.

<details><summary>Hint</summary>

"Every line is cookware" = "there is no line that isn't cookware". Then add a separate condition for "has at least one line".

</details>

---

## Exercise 12 ★★★: Debug the June report

A colleague wrote this report: "every shop with its delivered orders placed in June 2025 and the buyer's name; shops without such orders must still appear".

```sql
SELECT s.shop_name, o.id AS order_id, o.created_at::date AS ordered, u.full_name AS buyer
FROM sellers AS s
LEFT JOIN orders AS o ON o.seller_id = s.id
JOIN users AS u ON u.id = o.user_id
WHERE o.status = 'delivered'
  AND o.created_at >= '2025-06-01'
ORDER BY s.id, o.id;
```

It returns 7 rows and no shop without orders. Find **both** bugs, explain each one, and fix the query.

**Self-check:** the fixed query returns 9 rows: 7 orders plus Maple & Pine and Sakura Gadgets with `∅` in the other columns. Check that fixing only one of the two bugs is not enough.

<details><summary>Hint</summary>

Sections 5.3 and 5.4. One bug is about *where* a condition is written, the other about the *type* of a later join.

</details>

---

## Exercise 13 ★★★: Prices only ever went up

Find products whose **current price is higher than every price they were ever sold at**, using `> ALL`. Show the highest price they were sold at next to the current price. Think carefully about products that were never sold.

**Self-check:** exactly 1 row: product 52, Classic Zip Hoodie - Grey, now 49.00, sold at most at 45.00. If you get 10 rows, re-read section 11.

<details><summary>Hint</summary>

What does `x > ALL (empty set)` return? Add a condition that the product was sold at least once.

</details>
