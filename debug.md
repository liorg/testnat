
```bash
sudo docker run -d --name testnat --network whqueue-net -p 9380:8000 \
  -e WHQUEUE_BASE_URL=http://whqueue:8080 \
  -e PHONE_ID=aaaaaaaa-0000-0000-0000-00000000000e \
  -e PHONE_NUMBER_ID=111111 \
  -e WHQUEUE_MASTER_SECRET="$(sudo docker exec $CID cat /run/secrets/whqueue_master)" \
  liorgr/whatsapp-cloudapi-testnat:testNat

sudo docker logs testnat --tail 5

```
